"""The suspected-ASR-noise check, through the real command (issue #62).

`test_asr_flags.py` pins the detector. This runs `to_srt.py` and pins what a
person gets: a marker on the cue, the same timing, a count on stderr, a count in
the ledger on the success line, and the two ways to change the default.

Every fixture is synthetic.
"""

import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "to_srt.py"

RANGED = (
    "[00:00:00 - 00:00:05] Speaker 1: 今天我們來討論一個合成的問題。\n"
    "[00:00:05 - 00:00:46] Speaker 2: 中文。\n"
    "[00:00:46 - 00:00:50] Speaker 1: 謝謝觀看。\n"
    "[00:00:50 - 00:00:56] Speaker 2: 他自己他自己他自己他自己他自己\n"
    "[00:00:56 - 00:01:02] Speaker 1: 好的，我們繼續往下討論。\n"
)
CLEAN = (
    "[00:00:00 - 00:00:05] Speaker 1: 今天我們來討論一個合成的問題。\n"
    "[00:00:05 - 00:00:11] Speaker 2: 好的，我先說明第一個例子。\n"
    "[00:00:11 - 00:00:17] Speaker 1: 這個例子很清楚，請繼續。\n"
)
POINT = (
    "[00:00:00] Speaker 1: 今天我們來討論一個合成的問題。\n"
    "[00:00:05] Speaker 2: 謝謝觀看。\n"
    "[00:00:46] Speaker 1: 好的，我們繼續往下討論。\n"
)
MARKER = re.compile(r"\(疑似雜音/沉默幻覺 \[[^\]]*\]\) ?")


def squash(text: str) -> str:
    return "".join(text.split())


def blocks(srt: str) -> list[dict]:
    out = []
    for raw in srt.strip().split("\n\n"):
        lines = raw.split("\n")
        if len(lines) < 3:
            continue
        out.append({"timing": lines[1], "text": "\n".join(lines[2:])})
    return out


class Cli(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="flags-cli-")
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)
        self.cache = self.dir / "cache"
        self.cache.mkdir()

    def write(self, name: str, body: str) -> pathlib.Path:
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ,
               "PLAUD_CACHE_DIR": str(self.cache),
               "PLAUD_CONFIG": str(self.dir / "test-config.json")}
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True, env=env)

    def convert(self, body: str, *args: str) -> subprocess.CompletedProcess:
        path = self.write("t.md", body)
        return self.run_cli("--file", str(path), *args)


class TestTheDefaultMarksAndKeeps(Cli):
    def test_every_cue_survives_and_the_noisy_ones_carry_a_marker(self) -> None:
        p = self.convert(RANGED)
        self.assertEqual(0, p.returncode, p.stderr)
        cues = blocks(p.stdout)
        self.assertEqual(5, len(cues))
        self.assertEqual(
            "Speaker 2: (疑似雜音/沉默幻覺 [low_cps: 0.07 chars/s over 41s]) 中文。",
            " ".join(cues[1]["text"].split("\n")))
        # Line wrapping may split the marker across lines; compare as one line
        flat = [" ".join(c["text"].split("\n")) for c in cues]
        self.assertIn("[ghost_phrase]", flat[2])
        self.assertIn("[repetition: 3 characters × 5 in a row]", flat[3])
        for i in (0, 4):
            self.assertNotIn("疑似", cues[i]["text"])

    def test_timing_is_exactly_what_it_was(self) -> None:
        flagged = blocks(self.convert(RANGED).stdout)
        plain = blocks(self.convert(RANGED, "--no-flag").stdout)
        self.assertEqual([c["timing"] for c in plain], [c["timing"] for c in flagged])

    def test_the_words_are_untouched_only_a_marker_is_added(self) -> None:
        flagged = blocks(self.convert(RANGED).stdout)
        plain = blocks(self.convert(RANGED, "--no-flag").stdout)
        # Counted first: zip() of an empty list is a loop that never runs, and
        # an unrecognised option makes the plain run empty
        self.assertEqual((5, 5), (len(plain), len(flagged)))
        for before, after in zip(plain, flagged):
            self.assertEqual(squash(before["text"]), squash(MARKER.sub("", after["text"])))

    def test_the_marker_sits_between_the_speaker_and_the_words(self) -> None:
        text = " ".join(blocks(self.convert(RANGED).stdout)[2]["text"].split("\n"))
        self.assertRegex(text, r"^Speaker 1: \(疑似雜音/沉默幻覺 \[ghost_phrase\]\) 謝謝觀看。$")

    def test_no_speaker_still_shows_the_marker(self) -> None:
        text = " ".join(blocks(self.convert(RANGED, "--no-speaker").stdout)[1]["text"].split("\n"))
        self.assertEqual("(疑似雜音/沉默幻覺 [low_cps: 0.07 chars/s over 41s]) 中文。", text)

    def test_stderr_counts_what_was_flagged_by_signal(self) -> None:
        err = self.convert(RANGED).stderr
        self.assertRegex(err, r"⚠ 3 cue\(s\) flagged")
        for part in ("low_cps: 1", "ghost_phrase: 1", "repetition: 1"):
            self.assertIn(part, err)
        self.assertNotIn("中文", err)        # counts, never the words

    def test_the_success_line_carries_the_count(self) -> None:
        out = self.dir / "o.srt"
        p = self.convert(RANGED, "-o", str(out))
        self.assertEqual(0, p.returncode, p.stderr)
        self.assertRegex(p.stdout, r"wrote 5 cues \(3 cue\(s\) flagged as suspected ASR noise — see stderr\)")
        self.assertEqual(5, len(blocks(out.read_text(encoding="utf-8"))))

    def test_a_clean_transcript_is_unchanged_and_quiet(self) -> None:
        with_check = self.convert(CLEAN)
        without = self.convert(CLEAN, "--no-flag")
        self.assertEqual(0, with_check.returncode, with_check.stderr)
        self.assertEqual(without.stdout, with_check.stdout)
        self.assertEqual(without.stderr, with_check.stderr)
        self.assertNotIn("疑似", with_check.stdout)


MIXED = (
    "[00:00:00 - 00:00:05] Speaker 1: 今天我們來討論一個合成的問題。\n"
    "[00:00:05] Speaker 2: 好的，我先說明第一個例子。\n"
    "[00:00:11 - 00:00:17] Speaker 1: 這個例子很清楚，請繼續。\n"
)


class TestNoEndTimes(Cli):
    def test_the_phrase_signal_still_fires_without_ends(self) -> None:
        p = self.convert(POINT)
        self.assertEqual(0, p.returncode, p.stderr)
        self.assertRegex(p.stderr, r"⚠ 1 cue\(s\) flagged")

    def test_a_transcript_with_no_ends_at_all_gets_no_note(self) -> None:
        # The point form is a known kind from a known writer, and a clean run is
        # quiet (TestTheHeaderBucketIsReportedNegatively holds the tool to that)
        self.assertNotIn("could not run", self.convert(POINT).stderr)

    def test_a_mix_of_ends_and_no_ends_says_how_many_could_not_be_checked(self) -> None:
        p = self.convert(MIXED)
        self.assertEqual(0, p.returncode, p.stderr)
        self.assertRegex(p.stderr, r"note: .*could not run on 1 of 3")

    def test_a_file_with_ends_does_not_get_the_note(self) -> None:
        self.assertNotIn("could not run", self.convert(CLEAN).stderr)

    def test_stripping_does_not_stretch_the_cue_before_the_removed_one(self) -> None:
        kept = blocks(self.convert(POINT).stdout)
        stripped = blocks(self.convert(POINT, "--strip-hallucinations").stdout)
        self.assertEqual((3, 2), (len(kept), len(stripped)))
        self.assertEqual("00:00:00,000 --> 00:00:05,000", kept[0]["timing"])
        self.assertEqual("00:00:00,000 --> 00:00:05,000", stripped[0]["timing"])
        self.assertEqual(2, len(stripped))


class TestStrip(Cli):
    def test_flagged_cues_are_dropped_and_counted(self) -> None:
        out = self.dir / "s.srt"
        p = self.convert(RANGED, "--strip-hallucinations", "-o", str(out))
        self.assertEqual(0, p.returncode, p.stderr)
        text = out.read_text(encoding="utf-8")
        self.assertEqual(2, len(blocks(text)))
        for gone in ("中文。", "謝謝觀看", "他自己"):
            self.assertNotIn(gone, text)
        self.assertRegex(p.stdout, r"wrote 2 cues \(3 cue\(s\) removed as suspected ASR noise — see stderr\)")
        self.assertRegex(p.stderr, r"⚠ 3 cue\(s\) removed")

    def test_the_ledger_closes(self) -> None:
        # cues written + cues removed = every non-blank input line
        p = self.convert(RANGED, "--strip-hallucinations", "-o", str(self.dir / "s.srt"))
        self.assertRegex(p.stdout, r"wrote \d+ cues")
        self.assertRegex(p.stdout, r"\d+ cue\(s\) removed")
        written = int(re.search(r"wrote (\d+) cues", p.stdout).group(1))
        removed = int(re.search(r"(\d+) cue\(s\) removed", p.stdout).group(1))
        non_blank = len([l for l in RANGED.split("\n") if l.strip()])
        self.assertEqual(non_blank, written + removed)

    def test_removing_every_cue_is_stated_not_silent(self) -> None:
        p = self.convert("[00:00:00 - 00:00:30] Speaker 1: 中文。\n",
                         "--strip-hallucinations", "-o", str(self.dir / "z.srt"))
        self.assertEqual(0, p.returncode, p.stderr)
        self.assertRegex(p.stdout, r"wrote 0 cues \(1 cue\(s\) removed")


class TestTurningItOff(Cli):
    def test_no_flag_leaves_the_output_as_it_was(self) -> None:
        p = self.convert(RANGED, "--no-flag")
        self.assertEqual(0, p.returncode, p.stderr)
        self.assertEqual(5, len(blocks(p.stdout)))
        self.assertNotIn("疑似", p.stdout)
        self.assertNotIn("flagged", p.stderr)
        self.assertNotIn("could not run", p.stderr)

    def test_the_two_options_that_contradict_are_refused(self) -> None:
        p = self.convert(RANGED, "--no-flag", "--strip-hallucinations")
        self.assertEqual(2, p.returncode)
        self.assertEqual("", p.stdout)
        self.assertIn("--no-flag", p.stderr)
        self.assertIn("--strip-hallucinations", p.stderr)
        # An option the parser has never heard of also exits 2 and also names
        # both, so the message has to be ours
        self.assertNotIn("unrecognized", p.stderr)


class TestExtraPhrases(Cli):
    def test_a_phrase_from_a_file_is_flagged_only_when_the_file_is_given(self) -> None:
        body = "[00:00:00 - 00:00:05] Speaker 1: 合成幻影詞。\n[00:00:05 - 00:00:11] Speaker 2: 好的，我先說明。\n"
        extra = self.write("extra.txt", "# mine\n合成幻影詞\n")
        plain = self.convert(body)
        given = self.convert(body, "--ghost-phrases", str(extra))
        self.assertNotIn("疑似", plain.stdout)
        self.assertIn("[ghost_phrase]", given.stdout)
        self.assertRegex(given.stderr, r"⚠ 1 cue\(s\) flagged")

    def test_a_list_that_cannot_be_read_stops_the_run_and_names_it(self) -> None:
        p = self.convert(CLEAN, "--ghost-phrases", str(self.dir / "absent-list.txt"))
        self.assertNotEqual(0, p.returncode)
        self.assertEqual("", p.stdout)
        self.assertIn("absent-list.txt", p.stderr)

    def test_a_list_with_the_check_turned_off_is_refused(self) -> None:
        extra = self.write("extra.txt", "合成幻影詞\n")
        p = self.convert(CLEAN, "--no-flag", "--ghost-phrases", str(extra))
        self.assertEqual(2, p.returncode)
        self.assertIn("--ghost-phrases", p.stderr)
        self.assertNotIn("unrecognized", p.stderr)


class TestPreviewSourcesIsUntouched(Cli):
    def test_the_two_preview_lines_carry_no_marker(self) -> None:
        # Preview pairs polished and verbatim cues line by line (#58). A marker
        # on one side only would make every flagged cue look like a difference.
        (self.cache / "polish").mkdir()
        (self.cache / "rec1.md").write_text(
            "[00:00:05 - 00:00:46] Speaker 2: 中文。\n", encoding="utf-8")
        (self.cache / "polish" / "rec1.md").write_text(
            "[00:00:05 - 00:00:46] Speaker 2: 中文\n", encoding="utf-8")
        p = self.run_cli("rec1", "--preview-sources")
        self.assertEqual(0, p.returncode, p.stderr)
        self.assertIn("polished:", p.stdout)
        self.assertNotIn("疑似", p.stdout)


if __name__ == "__main__":
    unittest.main()
