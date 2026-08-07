"""Tests for scripts/to_srt.py (issue #4).

Subtitle timing is the kind of thing that looks right and is off by a second, so
these pin the arithmetic and the edge cases rather than smoke-testing that it
produces *some* output.
"""

import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "to_srt.py"

_spec = importlib.util.spec_from_file_location("to_srt", SCRIPT)
to_srt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(to_srt)


class TestTimestampParsing(unittest.TestCase):
    def test_accepts_the_forms_the_cache_actually_contains(self) -> None:
        cases = {
            "00:00:00": 0.0,
            "00:01:30": 90.0,
            "1:02:03": 3723.0,
            "02:03": 123.0,            # MM:SS short form
            "00:00:01.250": 1.25,
            "00:00:01,250": 1.25,      # comma decimal, as some tools emit
        }
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertAlmostEqual(to_srt.parse_timestamp(raw), want, places=3)

    def test_rejects_garbage(self) -> None:
        for raw in ("", "abc", "1", "1:2:3:4"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    to_srt.parse_timestamp(raw)


class TestTimestampFormatting(unittest.TestCase):
    def test_renders_srt_shape(self) -> None:
        self.assertEqual(to_srt.format_timestamp(0), "00:00:00,000")
        self.assertEqual(to_srt.format_timestamp(90.5), "00:01:30,500")
        self.assertEqual(to_srt.format_timestamp(3723.25), "01:02:03,250")

    def test_negative_clamps_instead_of_wrapping(self) -> None:
        # Wrapping would silently produce 23:59:59 and put the cue at the end of
        # a 24-hour timeline, which players accept and humans never find.
        self.assertEqual(to_srt.format_timestamp(-5), "00:00:00,000")

    def test_rounds_rather_than_truncates(self) -> None:
        self.assertEqual(to_srt.format_timestamp(1.0006), "00:00:01,001")


class TestSegmentParsing(unittest.TestCase):
    def test_extracts_timestamp_speaker_and_text(self) -> None:
        segs = to_srt.parse_segments("[00:00:05] Speaker 1: hello there\n")
        self.assertEqual(len(segs), 1)
        self.assertEqual(segs[0]["start"], 5.0)
        self.assertEqual(segs[0]["speaker"], "Speaker 1")
        self.assertEqual(segs[0]["text"], "hello there")

    def test_speaker_is_optional(self) -> None:
        segs = to_srt.parse_segments("[00:00:05] no speaker label here\n")
        self.assertEqual(segs[0]["speaker"], "")
        self.assertEqual(segs[0]["text"], "no speaker label here")

    def test_text_containing_colons_survives(self) -> None:
        segs = to_srt.parse_segments("[00:00:05] Ann: the ratio is 3:1 and rising\n")
        self.assertEqual(segs[0]["speaker"], "Ann")
        self.assertEqual(segs[0]["text"], "the ratio is 3:1 and rising")

    def test_non_segment_lines_are_dropped(self) -> None:
        body = "Subject: a meeting\n\n[00:00:05] A: real line\nrandom prose\n"
        self.assertEqual(len(to_srt.parse_segments(body)), 1)

    def test_frontmatter_is_stripped_before_parsing(self) -> None:
        raw = "---\nid: rec1\ncomplete: true\n---\n\n[00:00:05] A: hi\n"
        self.assertEqual(len(to_srt.parse_segments(to_srt.strip_frontmatter(raw))), 1)

    def test_cjk_text_survives(self) -> None:
        segs = to_srt.parse_segments("[00:01:02] 講者一: 我們把預算拆成兩期\n")
        self.assertEqual(segs[0]["speaker"], "講者一")
        self.assertEqual(segs[0]["text"], "我們把預算拆成兩期")


class TestCueBuilding(unittest.TestCase):
    def _segs(self, *starts: float) -> list[dict]:
        return [{"start": s, "speaker": "A", "text": f"line {i}"}
                for i, s in enumerate(starts)]

    def test_a_cue_ends_where_the_next_begins(self) -> None:
        cues = to_srt.build_cues(self._segs(0.0, 5.0, 12.0))
        self.assertEqual(cues[0]["end"], 5.0)
        self.assertEqual(cues[1]["end"], 12.0)

    def test_final_cue_uses_the_tail_estimate(self) -> None:
        cues = to_srt.build_cues(self._segs(0.0, 10.0), tail_seconds=3.0)
        self.assertEqual(cues[-1]["end"], 13.0)

    def test_out_of_order_timestamps_get_a_positive_duration(self) -> None:
        # A zero- or negative-length cue is rejected by players, so the line would
        # disappear entirely rather than merely being mistimed.
        cues = to_srt.build_cues(self._segs(10.0, 4.0), min_duration=0.5)
        self.assertGreater(cues[0]["end"], cues[0]["start"])
        self.assertEqual(cues[0]["end"], 10.5)

    def test_duplicate_timestamps_also_get_a_positive_duration(self) -> None:
        cues = to_srt.build_cues(self._segs(7.0, 7.0), min_duration=0.5)
        self.assertEqual(cues[0]["end"], 7.5)

    def test_speaker_prefix_can_be_omitted(self) -> None:
        with_speaker = to_srt.build_cues(self._segs(0.0))
        without = to_srt.build_cues(self._segs(0.0), show_speaker=False)
        self.assertTrue(with_speaker[0]["text"].startswith("A: "))
        self.assertFalse(without[0]["text"].startswith("A: "))


class TestRender(unittest.TestCase):
    def test_produces_wellformed_srt(self) -> None:
        cues = [{"start": 0.0, "end": 2.5, "text": "first"},
                {"start": 2.5, "end": 4.0, "text": "second"}]
        out = to_srt.render_srt(cues)
        self.assertEqual(
            out,
            "1\n00:00:00,000 --> 00:00:02,500\nfirst\n\n"
            "2\n00:00:02,500 --> 00:00:04,000\nsecond\n",
        )

    def test_indices_start_at_one_and_increment(self) -> None:
        cues = [{"start": float(i), "end": i + 1.0, "text": "x"} for i in range(3)]
        self.assertEqual(
            [ln for ln in to_srt.render_srt(cues).splitlines() if ln.isdigit()],
            ["1", "2", "3"],
        )


class TestCli(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="srt-test-")
        self.addCleanup(self._tmp.cleanup)
        self.cache = pathlib.Path(self._tmp.name)

    def _run(self, *args: str, cache=None) -> subprocess.CompletedProcess:
        env = dict(os.environ, PLAUD_CACHE_DIR=str(cache or self.cache))
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True, env=env)

    def _write(self, rec_id: str, body: str, front: str = "") -> None:
        (self.cache / f"{rec_id}.md").write_text(front + body)

    def test_converts_a_cached_recording(self) -> None:
        self._write("rec1", "[00:00:01] A: hello\n[00:00:04] B: world\n")
        p = self._run("rec1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("00:00:01,000 --> 00:00:04,000", p.stdout)
        self.assertIn("A: hello", p.stdout)

    def test_missing_recording_fails_with_a_useful_message(self) -> None:
        p = self._run("nope")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("not found", p.stderr)

    def test_refuses_a_traversing_id(self) -> None:
        p = self._run("../../etc/passwd")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("unsafe recording id", p.stderr)

    def test_transcript_without_timestamps_fails_loudly(self) -> None:
        # Emitting an empty .srt would look like success and produce a video with
        # no subtitles and no explanation.
        self._write("plain", "just prose, no timestamps at all\n")
        p = self._run("plain")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("no timestamped segments", p.stderr)

    def test_incomplete_cache_warns_on_stderr_but_still_converts(self) -> None:
        self._write("part", "[00:00:01] A: half a transcript\n",
                    front="---\nid: part\ncomplete: false\n---\n\n")
        p = self._run("part")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("marked incomplete", p.stderr)
        self.assertIn("00:00:01,000", p.stdout)

    def test_output_flag_writes_a_file(self) -> None:
        self._write("rec1", "[00:00:01] A: hello\n")
        out = self.cache / "out.srt"
        p = self._run("rec1", "-o", str(out))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("1 cues", p.stdout)
        self.assertIn("A: hello", out.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)


# --------------------------------------------------------------------------
# Language-dependent line length (#14)
#
# Subtitle readability conventions are not universal. Latin scripts run ~42
# characters per line; CJK is roughly half that in character count because each
# glyph is full-width. Emitting one unwrapped line regardless of script is the
# same mistake in both directions — unreadably long for Latin, and wrong by a
# factor of two for CJK.
# --------------------------------------------------------------------------
class TestScriptDetection(unittest.TestCase):
    def test_latin_text(self):
        self.assertEqual("latin", to_srt.detect_script("we should split the budget"))

    def test_chinese_text(self):
        self.assertEqual("cjk", to_srt.detect_script("我們應該把預算拆成兩期"))

    def test_japanese_kana(self):
        self.assertEqual("cjk", to_srt.detect_script("よろしくお願いします"))

    def test_thai_text(self):
        self.assertEqual("thai", to_srt.detect_script("การประชุมเรื่องงบประมาณ"))

    def test_mixed_text_follows_the_majority(self):
        """A Chinese sentence with an English term in it is still a Chinese line."""
        self.assertEqual("cjk", to_srt.detect_script("那就先把 budget 拆成兩期比較好"))

    def test_empty_text_defaults_to_latin(self):
        self.assertEqual("latin", to_srt.detect_script(""))


class TestWrapCueText(unittest.TestCase):
    def test_short_latin_line_is_untouched(self):
        self.assertEqual("hello there", to_srt.wrap_cue_text("hello there"))

    def test_long_latin_wraps_on_spaces(self):
        text = "we agreed to split the budget across two quarters and revisit it in March"
        out = to_srt.wrap_cue_text(text)
        self.assertIn("\n", out)
        for line in out.split("\n"):
            self.assertLessEqual(len(line), 42, line)
        self.assertEqual(text.split(), out.replace("\n", " ").split())

    def test_latin_never_splits_a_word(self):
        out = to_srt.wrap_cue_text("supercalifragilistic " * 4)
        for line in out.split("\n"):
            for word in line.split():
                self.assertIn(word, "supercalifragilistic")

    def test_cjk_uses_a_shorter_limit(self):
        """Full-width glyphs take about twice the space per character."""
        text = "我們應該把預算拆成兩期然後在三月的時候重新檢視這件事情比較妥當"
        out = to_srt.wrap_cue_text(text)
        self.assertIn("\n", out)
        for line in out.split("\n"):
            self.assertLessEqual(len(line), 20, line)

    def test_cjk_wraps_without_spaces(self):
        """CJK has no word spaces — wrapping on spaces would never fire."""
        text = "預算" * 30
        out = to_srt.wrap_cue_text(text)
        self.assertIn("\n", out)
        self.assertEqual(text, out.replace("\n", ""))

    def test_thai_is_left_unwrapped_and_that_is_deliberate(self):
        """Thai has no word spaces and needs a segmenter to break correctly.
        Breaking mid-word is worse than a long line, so it is left alone rather
        than broken wrongly."""
        text = "การประชุม" * 12
        self.assertEqual(text, to_srt.wrap_cue_text(text))

    def test_no_content_is_lost_for_any_script(self):
        """Checked per script, because "same content" means different things.
        Latin wraps AT a space, so the newline replaces one — compare tokens.
        CJK wraps BETWEEN characters with no space involved, so replacing the
        newline with a space would invent one — compare the raw string."""
        for text in ["a b c " * 30, "hello"]:
            self.assertEqual(text.split(), to_srt.wrap_cue_text(text).replace("\n", " ").split(), text)
        for text in ["預算" * 40, "よろしく" * 20]:
            self.assertEqual(text, to_srt.wrap_cue_text(text).replace("\n", ""), text)

    def test_wrapping_is_applied_when_rendering(self):
        cues = [{"start": 0.0, "end": 4.0,
                 "text": "we agreed to split the budget across two quarters and revisit in March"}]
        out = to_srt.render_srt(cues)
        body = [l for l in out.splitlines() if l and "-->" not in l and not l.strip().isdigit()]
        self.assertGreater(len(body), 1, out)
        for line in body:
            self.assertLessEqual(len(line), 42, line)


# --------------------------------------------------------------------------
# Subtitle source preference (#22)
#
# Plaud returns two versions of the same speech with identical segments and
# timings: raw, and a filler-thinned polish. Subtitles want the tidy one —
# nobody reads "呃" on screen — while search keeps the raw one, because search
# answers "what was said".
# --------------------------------------------------------------------------
class TestSubtitleSource(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = pathlib.Path(self._tmp.name)
        patch = mock.patch.object(to_srt, "CACHE_DIR", self.cache)
        patch.start()
        self.addCleanup(patch.stop)

    def _write(self, rel: str, text: str) -> pathlib.Path:
        p = self.cache / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def test_polish_is_preferred_when_present(self):
        self._write("rec_a.md", "[00:00:01] Speaker 1: raw words\n")
        self._write("polish/rec_a.md", "[00:00:01] Speaker 1: tidy words\n")
        self.assertEqual(self.cache / "polish" / "rec_a.md", to_srt.subtitle_source("rec_a"))

    def test_transcript_is_used_when_no_polish(self):
        self._write("rec_b.md", "[00:00:01] Speaker 1: raw words\n")
        self.assertEqual(self.cache / "rec_b.md", to_srt.subtitle_source("rec_b"))

    def test_missing_recording_returns_the_transcript_path(self):
        """So the caller's own 'not cached' error still fires, with the path the
        user expects to see named in it."""
        self.assertEqual(self.cache / "rec_c.md", to_srt.subtitle_source("rec_c"))

    def test_empty_polish_file_is_not_preferred(self):
        """A zero-byte polish would silently produce an empty subtitle file —
        the failure that looks like success."""
        self._write("rec_d.md", "[00:00:01] Speaker 1: raw words\n")
        self._write("polish/rec_d.md", "")
        self.assertEqual(self.cache / "rec_d.md", to_srt.subtitle_source("rec_d"))

    def test_the_cli_actually_uses_the_preference(self):
        """Testing `subtitle_source` alone proves the part works, not that it is
        wired in — deleting the call from main() left every other test green.
        Exercise the command, not the helper."""
        self._write("rec_e.md", "[00:00:01] Speaker 1: 呃 那個 raw wording\n")
        self._write("polish/rec_e.md", "[00:00:01] Speaker 1: tidy wording\n")
        out = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "to_srt.py"), "rec_e"],
            capture_output=True, text=True,
            env={**os.environ, "PLAUD_CACHE_DIR": str(self.cache)},
        )
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("tidy wording", out.stdout)
        self.assertNotIn("raw wording", out.stdout)

    def test_the_cli_falls_back_when_no_polish(self):
        self._write("rec_f.md", "[00:00:01] Speaker 1: only the raw exists\n")
        out = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "to_srt.py"), "rec_f"],
            capture_output=True, text=True,
            env={**os.environ, "PLAUD_CACHE_DIR": str(self.cache)},
        )
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("only the raw exists", out.stdout)


# --------------------------------------------------------------------------
# Subtitle source as a preference, not a decision (#29)
#
# #22 established the split — subtitles from the polish, search from the raw —
# and hardcoded it. Which one your *subtitles* use is a preference, though:
# clean reads better on screen, verbatim keeps the disfluency that qualitative
# work measures. Both answers are right; which one depends on the work.
#
# Search is NOT configurable and deliberately so: polish is the same speech
# reworded, so searching it returns sentences nobody said. That is #28's
# unanswered labelling question, not a preference.
# --------------------------------------------------------------------------
class TestSourcePreference(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = pathlib.Path(self._tmp.name)
        patch = mock.patch.object(to_srt, "CACHE_DIR", self.cache)
        patch.start()
        self.addCleanup(patch.stop)

    def _write(self, rel: str, text: str) -> pathlib.Path:
        p = self.cache / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def _both(self, rec_id: str) -> None:
        self._write(f"{rec_id}.md", "[00:00:01] Speaker 1: 呃 那個 就是 raw wording\n")
        self._write(f"polish/{rec_id}.md", "[00:00:01] Speaker 1: tidy wording\n")

    def test_polished_is_still_the_default(self) -> None:
        """The default must not move — an existing user with no config file has
        to see exactly what they saw yesterday."""
        self._both("r1")
        self.assertEqual(self.cache / "polish" / "r1.md", to_srt.subtitle_source("r1"))

    def test_verbatim_takes_the_raw_transcript_even_though_polish_exists(self) -> None:
        self._both("r2")
        self.assertEqual(self.cache / "r2.md",
                         to_srt.subtitle_source("r2", prefer="verbatim"))

    def test_verbatim_with_no_polish_is_still_the_raw_transcript(self) -> None:
        self._write("r3.md", "[00:00:01] Speaker 1: only raw\n")
        self.assertEqual(self.cache / "r3.md",
                         to_srt.subtitle_source("r3", prefer="verbatim"))


class TestDifferingSample(unittest.TestCase):
    """The pair of lines that makes the question answerable.

    Asking "polished or verbatim?" in the abstract is not answerable — the user
    has not seen either. Asking it beside the same line rendered both ways is.
    That is the whole reason this function exists.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = pathlib.Path(self._tmp.name)
        patch = mock.patch.object(to_srt, "CACHE_DIR", self.cache)
        patch.start()
        self.addCleanup(patch.stop)

    def _write(self, rel: str, text: str) -> None:
        p = self.cache / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def test_returns_the_first_segment_that_actually_differs(self) -> None:
        """Not the first segment — the first *differing* one. A recording whose
        opening line has no fillers would otherwise preview two identical lines
        and demonstrate nothing."""
        self._write("s1.md",
                    "[00:00:01] A: same opening\n[00:00:05] A: 呃 那個 the budget\n")
        self._write("polish/s1.md",
                    "[00:00:01] A: same opening\n[00:00:05] A: the budget\n")
        sample = to_srt.differing_sample("s1")
        self.assertIsNotNone(sample)
        self.assertIn("呃 那個 the budget", sample["verbatim"])
        self.assertEqual("A: the budget", sample["polished"])

    def test_returns_none_when_there_is_no_polish(self) -> None:
        self._write("s2.md", "[00:00:01] A: only raw\n")
        self.assertIsNone(to_srt.differing_sample("s2"))

    def test_returns_none_when_the_two_versions_are_identical(self) -> None:
        """The fifth state the issue's table did not list. A recording with no
        fillers to thin has nothing to choose between — asking anyway would be
        a question with no information in it."""
        text = "[00:00:01] A: nothing to thin here\n"
        self._write("s3.md", text)
        self._write("polish/s3.md", text)
        self.assertIsNone(to_srt.differing_sample("s3"))

    def test_returns_none_when_polish_is_empty(self) -> None:
        self._write("s4.md", "[00:00:01] A: raw\n")
        self._write("polish/s4.md", "")
        self.assertIsNone(to_srt.differing_sample("s4"))


class TestConfigurableLineLimits(unittest.TestCase):
    def test_default_limits_still_apply_when_none_passed(self) -> None:
        """The existing single-argument call sites must keep working unchanged."""
        text = "一二三四五六七八九十一二三四五六七八九十一二三"
        self.assertEqual(to_srt.wrap_cue_text(text),
                         to_srt.wrap_cue_text(text, limits=to_srt.LINE_LIMITS))

    def test_a_narrower_cjk_limit_wraps_sooner(self) -> None:
        text = "一二三四五六七八九十一二"      # 12 chars: under the default 20
        self.assertNotIn("\n", to_srt.wrap_cue_text(text))
        wrapped = to_srt.wrap_cue_text(text, limits={"latin": 42, "cjk": 5})
        self.assertIn("\n", wrapped)
        self.assertTrue(all(len(line) <= 5 for line in wrapped.split("\n")))

    def test_a_narrower_latin_limit_wraps_sooner(self) -> None:
        text = "the quick brown fox jumps over the lazy dog"
        wrapped = to_srt.wrap_cue_text(text, limits={"latin": 12, "cjk": 20})
        self.assertTrue(all(len(line) <= 12 for line in wrapped.split("\n")
                            if " " in line or len(line) <= 12))

    def test_render_srt_threads_the_limits_through(self) -> None:
        """Testing wrap_cue_text alone proves the part works, not that render_srt
        hands it anything — the #22 lesson, applied one layer up."""
        cues = [{"start": 0.0, "end": 2.0, "text": "一二三四五六七八九十"}]
        self.assertIn("\n", to_srt.render_srt(cues, limits={"latin": 42, "cjk": 3}))


class TestSourcePreferenceCLI(unittest.TestCase):
    """The wiring, not the parts.

    #22 shipped a passing test for `subtitle_source()` while `main()` could have
    stopped calling it entirely without a single test going red. Everything here
    runs the actual command.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = pathlib.Path(self._tmp.name)
        self.config = self.cache / "config.json"

    def _write(self, rel: str, text: str) -> None:
        p = self.cache / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def _both(self, rec_id: str) -> None:
        self._write(f"{rec_id}.md", "[00:00:01] Speaker 1: 呃 raw wording\n")
        self._write(f"polish/{rec_id}.md", "[00:00:01] Speaker 1: tidy wording\n")

    def _run(self, *args: str, **env_extra: str) -> subprocess.CompletedProcess:
        env = {**os.environ,
               "PLAUD_CACHE_DIR": str(self.cache),
               "PLAUD_CONFIG": str(self.config),
               **env_extra}
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True, env=env)

    def _set_config(self, **keys) -> None:
        self.config.write_text(json.dumps(keys), encoding="utf-8")

    def test_config_file_preference_reaches_the_output(self) -> None:
        self._both("c1")
        self._set_config(subtitle_source="verbatim")
        out = self._run("c1")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("raw wording", out.stdout)
        self.assertNotIn("tidy wording", out.stdout)

    def test_flag_beats_config_file(self) -> None:
        self._both("c2")
        self._set_config(subtitle_source="verbatim")
        out = self._run("c2", "--source", "polished")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("tidy wording", out.stdout)

    def test_env_beats_config_file(self) -> None:
        self._both("c3")
        self._set_config(subtitle_source="polished")
        out = self._run("c3", PLAUD_SUBTITLE_SOURCE="verbatim")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("raw wording", out.stdout)

    def test_flag_beats_env(self) -> None:
        self._both("c4")
        out = self._run("c4", "--source", "polished", PLAUD_SUBTITLE_SOURCE="verbatim")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("tidy wording", out.stdout)

    def test_no_config_still_means_polished(self) -> None:
        self._both("c5")
        out = self._run("c5")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("tidy wording", out.stdout)

    def test_line_limits_from_config_reach_the_output(self) -> None:
        self._write("c6.md", "[00:00:01] A: 一二三四五六七八九十\n")
        self._set_config(srt_line_limits={"cjk": 3})
        out = self._run("c6", "--no-speaker")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("一二三\n", out.stdout)

    def test_preview_sources_prints_both_and_exits_zero(self) -> None:
        self._both("c7")
        out = self._run("c7", "--preview-sources")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("raw wording", out.stdout)
        self.assertIn("tidy wording", out.stdout)

    def test_preview_sources_exits_3_when_there_is_no_choice(self) -> None:
        """Exit 3, not exit 0 with empty output.

        An empty stdout that exits 0 is indistinguishable from success, and a
        caller branching on it would ask the user to choose between two things
        it never found.
        """
        self._write("c8.md", "[00:00:01] A: only raw\n")
        out = self._run("c8", "--preview-sources")
        self.assertEqual(3, out.returncode)

    def test_preview_sources_exits_3_when_versions_are_identical(self) -> None:
        text = "[00:00:01] A: nothing to thin\n"
        self._write("c9.md", text)
        self._write("polish/c9.md", text)
        out = self._run("c9", "--preview-sources")
        self.assertEqual(3, out.returncode)

    def test_unknown_config_key_warns_but_subtitles_still_come_out(self) -> None:
        """A typo costs you the preference, never the work."""
        self._both("c10")
        self._set_config(subtitle_soruce="verbatim")
        out = self._run("c10")
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("subtitle_soruce", out.stderr)
        self.assertIn("tidy wording", out.stdout, "the typo must not silently take effect")
