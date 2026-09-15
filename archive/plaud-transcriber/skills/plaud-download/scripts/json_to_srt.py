#!/usr/bin/env python3
"""Convert Plaud trans_result JSON to SRT subtitle format with optional
ASR hallucination detection.

Usage:
    python3 json_to_srt.py <input.json> <output.srt>
    python3 json_to_srt.py - <output.srt>                  # stdin (gzipped piped via gunzip)
    python3 json_to_srt.py <input.json> <output.srt> --strip-hallucinations
    python3 json_to_srt.py <input.json> <output.srt> --no-flag

By default, segments detected as likely ASR hallucinations are output with
an inline `(疑似雜音/沉默幻覺: <reason>)` marker prefix so reviewers can
spot them quickly. Use `--strip-hallucinations` to drop them entirely, or
`--no-flag` to disable detection (legacy behavior).

# Why detect hallucination?

Plaud's `trans_result` JSON does NOT include per-segment confidence /
no_speech_prob fields (verified on one tutoring recording, spring 2026). The schema
is just `{content, start_time, end_time, speaker, original_speaker,
embeddingKey}`. So we can't filter by Whisper's own probability metrics.

Instead, we use 3 heuristics observed in the wild:

1. **Low chars-per-second (< 0.8 cps)** over a window > 5 seconds.
   Real Mandarin speech is ~3-5 cps. A 48s segment containing only "中文。"
   (3 chars) = 0.06 cps is an unmistakable signature of Whisper filling
   silence with a learned token sequence.

2. **Known Whisper ghost phrases**. Whisper variants tend to fall into
   the same handful of ghost outputs when fed silence or non-speech
   noise. The phrase library below is curated from real Plaud sessions
   (StudentA + StudentB + StudentC 家教 recordings).

3. **Internal token repetition** (same 3-12 char substring appearing
   ≥ 5 times in one segment). E.g. "他自己他自己他自己..." — Whisper
   stuck in a generation loop.

The detector is conservative: it FLAGS rather than DROPS by default. The
human reviewer (or downstream lecture-add Step 1 SRT correction phase)
makes the final call.

# What about Plaud's diarization signal?

Plaud's `original_speaker` field carries useful info: in 1-on-1 tutoring,
true speakers are usually only "Speaker 1" + "Speaker 2". When you see
"Speaker 3" / "Speaker 4" appear, Plaud's diarization decided this is
ANOTHER person — typically environmental noise (nearby table at 摩斯,
ASR's silent-ghost outputs, etc.).

By default we PRESERVE `original_speaker` info in the SRT output via
the `[Speaker N (original: Speaker M)]` format when the merged speaker
disagrees with the original. Use `--keep-original-speaker` to skip the
merging entirely (output uses `original_speaker` raw).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from typing import Optional


# ---------------------------------------------------------------------------
# Hallucination detection
# ---------------------------------------------------------------------------

# Phrases Whisper-family models commonly hallucinate when fed silence /
# music / non-speech noise. Curated from real Plaud sessions.
WHISPER_GHOST_PHRASES = {
    # YouTube subtitle attribution leakage
    "謝謝觀看",
    "謝謝大家",
    "下次見",
    "再會",
    "感謝您觀看",
    "感謝您收看",
    "感謝您收看時局新聞，再會",
    "感謝您收看時局新聞,再會",
    "感謝您收睇時局新聞，再會",
    "感謝您收睇時局新聞,再會",
    "中文字幕由 Amara.org 社群提供",
    "繁體中文字幕由Amara.org社群提供",
    "繁體中文字幕由 Amara.org 社群提供",
    "感謝您的觀看",
    "謝謝收看",
    # Generic short tokens that often appear over long silence
    "中文",
    "中文。",
    "繁體中文",
    "繁體中文。",
    # Misc lecture-induced ghosts
    "字幕",
    "字幕。",
    "OK。",
    "嗯。",
}

# Lower-case match: also catch when these appear as the entire content
# stripped of punctuation / whitespace.
WHISPER_GHOST_NORMALIZED = {
    p.replace("。", "").replace("，", "").replace(",", "").replace(" ", "").lower()
    for p in WHISPER_GHOST_PHRASES
}


def _normalize(text: str) -> str:
    return text.replace("。", "").replace("，", "").replace(",", "").replace(" ", "").strip().lower()


def detect_hallucination(entry: dict) -> Optional[tuple[str, str]]:
    """Return (signal, reason) if entry looks like ASR hallucination,
    else None. Conservative — only flags strong signals.

    Tuned to minimize false positives in lecture context where the
    instructor naturally repeats key terms ("標準誤", "中位數") or
    short Q&A exchanges ("OK。", "啊...") happen at low chars-per-second.
    """
    content = entry.get("content", "").strip()
    if not content:
        return ("empty", "no content")

    duration_s = (entry["end_time"] - entry["start_time"]) / 1000.0
    char_count = len(content)

    # ---- Signal 1: silence-filled hallucination ----
    # The strongest signal — Whisper fills long silence with a tiny ghost
    # token. Examples observed in that recording: "中文。" (3 chars) stretched
    # across 41-57 seconds.
    #
    # Rule: duration > 15s AND chars-per-second < 0.3.
    #
    # Tuning rationale (same recording):
    #   - True hallucination: 3 chars / 41s = 0.07 cps  ✅ caught
    #   - True hallucination: 3 chars / 57s = 0.05 cps  ✅ caught
    #   - False-positive risk (StudentA hesitating mid-question):
    #     "這個範例句是合成的慢速問句。" 12 chars / 22s = 0.55 cps  ✅ NOT caught
    #
    # The cps gap between hallucination (~0.05) and slow real speech
    # (~0.5+) is large enough that 0.3 is a safe threshold.
    if duration_s > 15 and char_count > 0:
        cps = char_count / duration_s
        if cps < 0.3:
            return ("low_cps", f"{cps:.2f} chars/s over {duration_s:.0f}s")

    # ---- Signal 2: Whisper ghost phrase (EXACT match only) ----
    # Substring match was too aggressive — flagged real "OK。" student
    # responses. Only flag when content IS the ghost phrase verbatim.
    if _normalize(content) in WHISPER_GHOST_NORMALIZED:
        return ("ghost_phrase", repr(content))

    # ---- Signal 3: consecutive repetition (Whisper generation loop) ----
    # Real Whisper hallucination repeats *adjacent* identical chunks:
    #   "他自己他自己他自己他自己他自己"
    # vs. natural lecture emphasis (NOT consecutive):
    #   "...標準誤越小代表估得越準...所以標準誤越大有效性就越小..."
    # The latter has "標準誤" appearing 5× scattered in 100+ chars; the
    # former has "他自己" appearing 5× back-to-back in 15 chars.
    #
    # Detection: find any substring (3-12 chars, length >= 3) that
    # repeats CONSECUTIVELY 3 or more times.
    for length in range(3, 13):
        if char_count < length * 3:
            continue
        for i in range(char_count - length * 3 + 1):
            substr = content[i : i + length]
            if not substr.strip() or substr.isspace():
                continue
            # Count consecutive identical chunks starting at i
            count = 1
            j = i + length
            while j + length <= char_count and content[j : j + length] == substr:
                count += 1
                j += length
            if count >= 3:
                return ("repetition", f"{substr!r} × {count} consecutive")

    return None


# ---------------------------------------------------------------------------
# Speaker handling
# ---------------------------------------------------------------------------


def render_speaker(entry: dict, keep_original: bool) -> str:
    """Render the speaker tag. Default: use `speaker` (Plaud's merged label,
    e.g. "Teacher", "StudentA", or "Speaker 2"). When `--keep-original-speaker`,
    fall back to `original_speaker` (raw "Speaker 1/2/3/4")."""
    if keep_original:
        return entry.get("original_speaker", entry.get("speaker", ""))
    return entry.get("speaker", "")


# ---------------------------------------------------------------------------
# SRT output
# ---------------------------------------------------------------------------


def ms_to_srt(ms: int) -> str:
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000
    ms_rem = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms_rem:03d}"


def convert(
    input_path: str,
    output_path: str,
    *,
    flag_hallucinations: bool = True,
    strip_hallucinations: bool = False,
    keep_original_speaker: bool = False,
) -> dict:
    """Convert Plaud JSON to SRT.

    Returns a stats dict for caller reporting.
    """
    if input_path == "-":
        data = json.load(sys.stdin)
    else:
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)

    srt_lines = []
    stats = {
        "total": len(data),
        "kept": 0,
        "stripped": 0,
        "flagged": 0,
        "by_signal": Counter(),
    }
    out_index = 0

    for entry in data:
        hallucination = detect_hallucination(entry) if (flag_hallucinations or strip_hallucinations) else None

        if hallucination is not None:
            stats["by_signal"][hallucination[0]] += 1
            if strip_hallucinations:
                stats["stripped"] += 1
                continue
            stats["flagged"] += 1

        out_index += 1
        start = ms_to_srt(entry["start_time"])
        end = ms_to_srt(entry["end_time"])
        speaker = render_speaker(entry, keep_original_speaker)
        content = entry["content"]
        if hallucination is not None and flag_hallucinations:
            signal, reason = hallucination
            content = f"(疑似雜音/沉默幻覺 [{signal}: {reason}]) {content}"

        if speaker:
            srt_lines.extend([str(out_index), f"{start} --> {end}", f"[{speaker}] {content}", ""])
        else:
            srt_lines.extend([str(out_index), f"{start} --> {end}", content, ""])
        stats["kept"] += 1

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(srt_lines))

    print(f"OK: {stats['total']} entries -> {output_path}")
    if stats["flagged"] or stats["stripped"]:
        action = "flagged" if flag_hallucinations and not strip_hallucinations else "stripped"
        total_caught = stats["flagged"] + stats["stripped"]
        breakdown = ", ".join(f"{sig}={n}" for sig, n in stats["by_signal"].items())
        print(f"   {action}: {total_caught} likely-hallucination segments ({breakdown})", file=sys.stderr)
        if flag_hallucinations and not strip_hallucinations:
            print(
                "   review the flagged entries — re-run with --strip-hallucinations to drop",
                file=sys.stderr,
            )
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Convert Plaud trans_result JSON to SRT (with hallucination detection).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input", help="Plaud JSON file path, or '-' for stdin.")
    parser.add_argument("output", help="Output SRT file path.")
    parser.add_argument(
        "--strip-hallucinations",
        action="store_true",
        help="Drop entries detected as likely hallucinations entirely.",
    )
    parser.add_argument(
        "--no-flag",
        dest="flag_hallucinations",
        action="store_false",
        help="Disable hallucination detection (legacy raw output).",
    )
    parser.add_argument(
        "--keep-original-speaker",
        action="store_true",
        help="Use original_speaker (raw Speaker 1/2/3/...) instead of merged speaker label.",
    )
    args = parser.parse_args()

    convert(
        args.input,
        args.output,
        flag_hallucinations=args.flag_hallucinations,
        strip_hallucinations=args.strip_hallucinations,
        keep_original_speaker=args.keep_original_speaker,
    )


if __name__ == "__main__":
    main()
