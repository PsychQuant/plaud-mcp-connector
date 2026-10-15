"""Tests for scripts/asr_flags.py (issue #62).

ASR engines fill silence and noise with text nobody said: a three-character
"中文。" stretched across forty-one seconds, a subtitle credit, a phrase repeated
until the decoder gives up. This module marks those cues; it never decides that
a person did not speak, so everything here pins two things at once — that each
signal fires where it should, and that it stays quiet on the near misses that
look like it (slow real speech, laughter, a phrase inside a longer sentence).

Every fixture is synthetic. None of it is text from a recording.
"""

import functools
import importlib.util
import pathlib
import tempfile
import time
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
MODULE = REPO / "scripts" / "asr_flags.py"
PHRASES = REPO / "scripts" / "asr_ghost_phrases.txt"


@functools.lru_cache(maxsize=1)
def _load():
    if not MODULE.is_file():
        raise AssertionError(f"{MODULE.relative_to(REPO)} does not exist yet")
    spec = importlib.util.spec_from_file_location("asr_flags_under_test", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def seg(start, end, text, speaker="Speaker 1"):
    return {"start": float(start), "end": None if end is None else float(end),
            "speaker": speaker, "text": text}


class FlagsCase(unittest.TestCase):
    @property
    def flags(self):
        return _load()

    @property
    def phrases(self):
        return self.flags.load_phrases()

    def detect(self, text, duration):
        return self.flags.detect(text, duration, self.phrases)


class TestLowCharactersPerSecond(FlagsCase):
    """Real speech runs at several characters a second; a decoder holding a
    three-character token across a long silence runs at a few hundredths."""

    def test_a_short_token_stretched_over_a_long_window_is_flagged(self) -> None:
        flag = self.detect("中文。", 41.0)
        self.assertIsNotNone(flag)
        self.assertEqual("low_cps", flag.signal)
        self.assertEqual("0.07 chars/s over 41s", flag.detail)

    def test_slow_real_speech_is_not_flagged(self) -> None:
        text = "這個例句是慢速合成問句。"
        self.assertEqual(12, len(text))            # 12 characters over 22 s = 0.55/s
        self.assertIsNone(self.detect(text, 22.0))

    def test_fifteen_seconds_exactly_is_not_enough(self) -> None:
        # "more than 15 seconds", so the boundary itself stays quiet
        # Not "中文。": that is on the phrase list and would be flagged at any length
        self.assertIsNone(self.detect("一二三", 15.0))
        self.assertEqual("low_cps", self.detect("一二三", 15.01).signal)

    def test_a_rate_of_exactly_point_three_is_not_flagged(self) -> None:
        six = "一二三四五六"
        self.assertEqual(0.3, len(six) / 20.0)
        self.assertIsNone(self.detect(six, 20.0))
        self.assertEqual("low_cps", self.detect("一二三四五", 20.0).signal)

    def test_millisecond_timestamps_do_not_move_the_strict_boundaries(self) -> None:
        # 15.000 s as the difference of two floats is not always 15.0: about one start in
        # 150 past the first hour computes 15.000000000000455 (verify round 1)
        start = next(s / 1000 for s in range(4_081_000, 4_100_000)
                     if (s / 1000 + 15.0) - s / 1000 > 15.0)
        raw = (start + 15.0) - start
        self.assertGreater(raw, 15.0)       # the float difference is above the line; the cue is not
        out, report = self.flags.flag_segments(
            [seg(start, start + 15.0, "一二三")], phrases=self.phrases)
        self.assertEqual(0, report.flagged)

    def test_the_label_never_shows_a_rate_that_looks_like_it_is_not_under_the_line(self) -> None:
        # 5 characters over 16.69 s is 0.2996: shown as 0.29, not rounded up to 0.30
        flag = self.detect("一二三四五", 16.69)
        self.assertEqual("low_cps", flag.signal)
        self.assertTrue(flag.detail.startswith("0.29 chars/s over 16s"), flag.detail)

    def test_no_duration_means_the_signal_does_not_run(self) -> None:
        self.assertIsNone(self.detect("一二三", None))

    def test_text_that_displays_as_nothing_is_never_flagged(self) -> None:
        # build_cues removes these and counts them; flagging them as well
        # would count the same cue under two headings
        for text in ("", "   ", "​​", "\t ‍"):
            self.assertIsNone(self.detect(text, 600.0), repr(text))

    def test_visible_length_ignores_whitespace_and_format_characters(self) -> None:
        self.assertEqual(3, self.flags.visible_length("中 文​。"))
        self.assertEqual(0, self.flags.visible_length(" ​\t"))


class TestGhostPhrases(FlagsCase):
    def test_a_subtitle_credit_is_flagged_whatever_the_punctuation(self) -> None:
        for text in ("謝謝觀看", "謝謝觀看。", "謝 謝 觀 看！", "感謝您的觀看。",
                     "感謝您收看。", "謝謝收看。"):
            flag = self.detect(text, 3.0)
            self.assertIsNotNone(flag, text)
            self.assertEqual("ghost_phrase", flag.signal, text)
            self.assertEqual("", flag.detail)

    def test_the_amara_credit_is_flagged_with_or_without_spaces(self) -> None:
        for text in ("中文字幕由 Amara.org 社群提供", "繁體中文字幕由Amara.org社群提供",
                     "繁體中文字幕由 amara.org 社群提供"):
            self.assertEqual("ghost_phrase", self.detect(text, 5.0).signal, text)

    def test_a_bare_language_token_is_flagged(self) -> None:
        for text in ("中文", "中文。", "繁體中文", "字幕。"):
            self.assertEqual("ghost_phrase", self.detect(text, 2.0).signal, text)

    def test_the_whole_cue_must_be_the_phrase(self) -> None:
        for text in ("今天謝謝觀看這個影片的各位，我們下週再見",
                     "我想說的是中文。",
                     "這個詞在中文字幕裡怎麼翻"):
            self.assertIsNone(self.detect(text, 3.0), text)

    def test_a_signoff_the_prototype_listed_as_a_subtitle_leak_is_flagged(self) -> None:
        # Not on real material (0 of 3,667 cues), so nothing says people write them as
        # a whole cue, and the prototype put them with the subtitle credits
        for text in ("下次見。", "再會。"):
            self.assertEqual("ghost_phrase", self.detect(text, 2.0).signal, text)

    def test_ordinary_replies_are_not_on_the_list(self) -> None:
        # Left off because they WERE measured: 21 whole cues in 3,667 on one cache,
        # nearly all of them somebody answering
        for text in ("OK。", "嗯。", "謝謝大家。"):
            self.assertIsNone(self.detect(text, 2.0), text)

    def test_the_low_rate_signal_wins_when_both_apply(self) -> None:
        self.assertEqual("low_cps", self.detect("中文。", 41.0).signal)

    def test_the_shipped_list_is_loaded_and_normalised(self) -> None:
        phrases = self.flags.load_phrases()
        self.assertGreaterEqual(len(phrases), 8)
        self.assertIn(self.flags.normalise("謝謝觀看。"), phrases)
        for phrase in phrases:
            self.assertEqual(phrase, self.flags.normalise(phrase))
            self.assertNotEqual("", phrase)


class TestRepetition(FlagsCase):
    def test_a_phrase_repeated_back_to_back_is_flagged(self) -> None:
        flag = self.detect("他自己他自己他自己他自己他自己", 4.0)
        self.assertEqual("repetition", flag.signal)
        self.assertEqual("3 characters × 5 in a row", flag.detail)

    def test_five_copies_is_the_minimum(self) -> None:
        # The issue says three. On 3,667 real cues every unit it found at three or four
        # copies was ordinary emphasis, and the prototype's own prose says five; the issue's
        # example loop is five. A marker in the subtitles is not free.
        self.assertEqual("repetition", self.detect("他自己" * 5, 4.0).signal)
        self.assertIsNone(self.detect("他自己" * 4, 4.0))

    def test_emphasis_is_not_a_loop(self) -> None:
        for text in ("好的，好的，好的，我們繼續。", "No no no no, that's wrong.",
                     "yeah yeah yeah yeah", "いいえいいえいいえ"):
            self.assertIsNone(self.detect(text, 4.0), text)

    def test_emphasis_scattered_through_a_sentence_is_not_flagged(self) -> None:
        text = "標準誤越小代表估得越準，所以標準誤越大有效性就越小，標準誤也不是越小越好"
        self.assertIsNone(self.detect(text, 9.0))

    def test_laughter_and_interjections_are_not_flagged_even_with_separators(self) -> None:
        # A unit whose letters are all the same character is not a loop, however it is spaced
        for text in ("哈哈哈哈哈哈哈哈哈哈哈哈", "啊啊啊啊啊啊啊啊啊啊", "。。。。。。。。。。",
                     "哈 哈 哈 哈 哈 哈 哈 哈 哈", "哈…哈…哈…哈…哈…哈…哈…",
                     "ha ha ha ha ha ha ha ha", "he he he he he he he he"):
            self.assertIsNone(self.detect(text, 4.0), text)

    def test_a_unit_of_punctuation_alone_is_not_flagged(self) -> None:
        # Long enough that the detector reaches it: a 4-character unit needs 20 characters
        text = "，。，。" * 5
        self.assertEqual(20, len(text))
        self.assertIsNone(self.detect(text, 4.0))

    def test_the_unit_length_bounds(self) -> None:
        self.assertIsNone(self.detect("好的好的好的好的好的", 4.0))              # 2 characters
        twelve = "一二三四五六七八九十甲乙"
        thirteen = twelve + "丙"
        self.assertEqual("repetition", self.detect(twelve * 5, 4.0).signal)
        self.assertIsNone(self.detect(thirteen * 5, 4.0))

    def test_a_unit_with_fewer_than_three_distinct_letters_is_never_a_loop(self) -> None:
        # And so a two-character word repeated is not reported as a four-character unit
        for text in ("好的" * 10, "ha ha ha ha ha ha ha ha", "no no no no no no no", "ok ok ok ok ok ok ok"):
            self.assertIsNone(self.detect(text, 4.0), text)

    def test_the_repetition_can_sit_inside_a_longer_cue(self) -> None:
        text = "然後我們看下一頁" + "他自己" * 6 + "接著是結論"
        self.assertEqual("repetition", self.detect(text, 9.0).signal)


class TestAdversarialInput(FlagsCase):
    """`--file` bounds neither file size nor line length, so a cue can be as long
    as the file. The first scan of the archived script re-compared every window
    at every offset: a hundred thousand identical characters took minutes."""

    def timed(self, text):
        t0 = time.perf_counter()
        flag = self.detect(text, 4.0)
        return flag, time.perf_counter() - t0

    def test_a_hundred_thousand_identical_characters(self) -> None:
        flag, took = self.timed("a" * 100_000)
        self.assertIsNone(flag)
        self.assertLess(took, 3.0)

    def test_a_hundred_thousand_characters_of_a_three_character_loop(self) -> None:
        flag, took = self.timed("abc" * 33_000)
        self.assertEqual("repetition", flag.signal)
        self.assertLess(took, 3.0)

    def test_long_runs_of_whitespace(self) -> None:
        flag, took = self.timed(" " * 200_000)
        self.assertIsNone(flag)
        self.assertLess(took, 3.0)

    def test_a_long_text_with_nothing_repeating(self) -> None:
        text = "".join(chr(0x4E00 + i) for i in range(20_000)) * 5
        flag, took = self.timed(text)
        self.assertLess(took, 3.0)

    def test_a_cue_of_a_character_that_normalises_to_eighteen(self) -> None:
        # U+FDFA becomes 18 characters under NFKC. A 6 MB file of it as one cue took 7.5 s and
        # 2.6 GB (verify round 1): folding the whole cue to compare it with a phrase of a few
        # characters. A cue far longer than any phrase cannot be one.
        flag, took = self.timed("\ufdfa" * 2_000_000)
        self.assertLess(took, 2.0)

    def test_a_phrase_still_matches_up_to_the_length_that_is_folded(self) -> None:
        cap = self.flags.PHRASE_TEXT_CAP
        self.assertEqual("ghost_phrase", self.detect("謝謝觀看" + "。" * (cap - 8), 3.0).signal)

    def test_a_repetition_beyond_the_scanned_window_is_not_looked_for(self) -> None:
        # Stated limit, pinned so that changing it is a decision and not an accident.
        window = self.flags.REPEAT_WINDOW
        filler = "".join(chr(0x4E00 + i) for i in range(window))
        self.assertIsNone(self.detect(filler + "他自己" * 6, 4.0))
        self.assertEqual("repetition", self.detect("他自己" * 6 + filler, 4.0).signal)


class TestPhraseList(FlagsCase):
    def write(self, body, name="extra.txt", encoding="utf-8"):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = pathlib.Path(tmp.name) / name
        path.write_text(body, encoding=encoding)
        return path

    def test_the_shipped_file_exists_and_is_utf8(self) -> None:
        self.assertTrue(PHRASES.is_file())
        PHRASES.read_text(encoding="utf-8")

    def test_comments_and_blank_lines_are_ignored(self) -> None:
        path = self.write("# a comment\n\n   \n  # indented comment\n合成幻影詞\n")
        phrases = self.flags.load_phrases(path)
        self.assertIn(self.flags.normalise("合成幻影詞"), phrases)
        self.assertNotIn(self.flags.normalise("a comment"), phrases)

    def test_extra_phrases_add_to_the_shipped_ones(self) -> None:
        base = self.flags.load_phrases()
        phrases = self.flags.load_phrases(self.write("合成幻影詞\n"))
        self.assertTrue(base < phrases)
        self.assertEqual("ghost_phrase",
                         self.flags.detect("合成幻影詞。", 2.0, phrases).signal)
        self.assertIsNone(self.flags.detect("合成幻影詞。", 2.0, base))

    def test_matching_ignores_case_width_and_punctuation_on_both_sides(self) -> None:
        phrases = self.flags.load_phrases(self.write("Ｔｈａｎｋｓ，  For Watching!\n"))
        self.assertEqual("ghost_phrase",
                         self.flags.detect("thanks for watching", 2.0, phrases).signal)

    def test_a_line_with_nothing_left_after_normalising_is_dropped(self) -> None:
        # An empty entry would match every punctuation-only cue
        phrases = self.flags.load_phrases(self.write("。。\n！\n"))
        self.assertNotIn("", phrases)
        self.assertIsNone(self.flags.detect("。", 2.0, phrases))

    def test_a_byte_order_mark_is_tolerated(self) -> None:
        path = self.write("合成幻影詞\n", encoding="utf-8-sig")
        self.assertIn(self.flags.normalise("合成幻影詞"), self.flags.load_phrases(path))

    def test_an_unreadable_list_is_an_error_naming_the_path(self) -> None:
        missing = pathlib.Path(tempfile.gettempdir()) / "no-such-ghost-list-62.txt"
        with self.assertRaises(self.flags.PhraseListError) as ctx:
            self.flags.load_phrases(missing)
        self.assertIn("no-such-ghost-list-62.txt", str(ctx.exception))
        bad = self.write("")
        bad.write_bytes(b"\xff\xfe\x00bad")
        with self.assertRaises(self.flags.PhraseListError):
            self.flags.load_phrases(bad)


class TestTheStage(FlagsCase):
    def run_stage(self, segments, **kw):
        return self.flags.flag_segments(segments, phrases=self.phrases, **kw)

    def test_a_flagged_segment_keeps_its_place_timing_and_words(self) -> None:
        segs = [seg(0, 5, "今天我們討論一個問題。"), seg(5, 46, "中文。", "Speaker 2"),
                seg(46, 52, "好的，我們繼續。")]
        out, report = self.run_stage(segs)
        self.assertEqual(3, len(out))
        for before, after in zip(segs, out):
            for key in ("start", "end", "speaker", "text"):
                self.assertEqual(before[key], after[key])
        self.assertNotIn("flag", out[0])
        self.assertNotIn("flag", out[2])
        self.assertEqual("疑似雜音/沉默幻覺 [low_cps: 0.07 chars/s over 41s]",
                         out[1]["flag"])

    def test_the_input_is_not_mutated(self) -> None:
        segs = [seg(0, 41, "中文。")]
        snapshot = [dict(s) for s in segs]
        self.run_stage(segs)
        self.run_stage(segs, strip=True)
        self.assertEqual(snapshot, segs)

    def test_the_label_for_each_signal(self) -> None:
        label = self.flags.label
        F = self.flags.Flag
        self.assertEqual("疑似雜音/沉默幻覺 [ghost_phrase]", label(F("ghost_phrase", "")))
        self.assertEqual("疑似雜音/沉默幻覺 [repetition: 3 characters × 5 in a row]",
                         label(F("repetition", "3 characters × 5 in a row")))

    def test_the_marker_is_the_label_in_brackets(self) -> None:
        self.assertEqual("(疑似雜音/沉默幻覺 [ghost_phrase])",
                         self.flags.bracket("疑似雜音/沉默幻覺 [ghost_phrase]"))

    def test_the_duration_is_what_the_viewer_sees_not_the_declared_end(self) -> None:
        # Declared to run 100 s, but the next cue starts at 10 s: players see 10 s
        segs = [seg(0, 100, "中文。"), seg(10, 20, "我們繼續下一個問題吧各位。")]
        out, _ = self.run_stage(segs)
        self.assertIn("flag", out[0])                       # ghost phrase, not low_cps
        self.assertIn("[ghost_phrase]", out[0]["flag"])
        segs = [seg(0, 100, "一二三"), seg(10, 20, "我們繼續下一個問題吧各位。")]
        out, _ = self.run_stage(segs)
        self.assertNotIn("flag", out[0])
        segs = [seg(0, 100, "一二三")]                       # last cue: nothing to clamp it
        out, _ = self.run_stage(segs)
        self.assertIn("low_cps", out[0]["flag"])

    def test_a_segment_without_a_real_end_is_counted_not_guessed(self) -> None:
        segs = [seg(0, None, "一二三"), seg(60, None, "好的我們繼續今天的內容吧。"),
                seg(70, 80, "有結尾的一段話。")]
        out, report = self.run_stage(segs)
        self.assertEqual(0, report.flagged)
        self.assertEqual(2, report.unevaluable)
        self.assertEqual(3, report.total)

    def test_an_empty_segment_is_not_counted_as_unevaluable(self) -> None:
        out, report = self.run_stage([seg(0, None, "​"), seg(5, 9, "好的我們開始。")])
        self.assertEqual(0, report.unevaluable)

    def test_a_ghost_phrase_still_fires_without_an_end(self) -> None:
        out, report = self.run_stage([seg(0, None, "謝謝觀看。"), seg(5, None, "好的，開始。")])
        self.assertEqual(1, report.flagged)
        self.assertEqual({"ghost_phrase": 1}, {k: v for k, v in report.by_signal.items() if v})
        # The rate check could not run on either: flagging the first by another
        # signal does not mean the first was checked for silence
        self.assertEqual(2, report.unevaluable)

    def test_counts_per_signal(self) -> None:
        segs = [seg(0, 5, "今天我們討論一個問題。"), seg(5, 46, "中文。"),
                seg(46, 50, "謝謝觀看。"), seg(50, 56, "他自己他自己他自己他自己他自己"),
                seg(56, 60, "再來看第二個例子。")]
        out, report = self.run_stage(segs)
        self.assertEqual(5, report.total)
        self.assertEqual(3, report.flagged)
        self.assertEqual(0, report.stripped)
        self.assertEqual({"low_cps": 1, "ghost_phrase": 1, "repetition": 1},
                         dict(report.by_signal))

    def test_strip_removes_flagged_segments_and_says_how_many(self) -> None:
        segs = [seg(0, 5, "今天我們討論一個問題。"), seg(5, 46, "中文。"),
                seg(46, 50, "謝謝觀看。"), seg(50, 56, "好的，我們繼續。")]
        out, report = self.run_stage(segs, strip=True)
        self.assertEqual([0.0, 50.0], [s["start"] for s in out])
        self.assertTrue(all("flag" not in s for s in out))
        self.assertEqual(2, report.flagged)
        self.assertEqual(2, report.stripped)

    def test_stripping_does_not_stretch_the_cue_before_it(self) -> None:
        # With no declared end, a cue ends where the next one starts. Remove the
        # next one and the cue would swallow the whole removed span.
        segs = [seg(0, None, "今天我們討論一個問題。"), seg(5, None, "謝謝觀看。"),
                seg(46, None, "好的，我們繼續。")]
        out, _ = self.run_stage(segs, strip=True)
        self.assertEqual(5.0, out[0]["end"])
        self.assertIsNone(out[1]["end"])

    def test_stripping_leaves_a_declared_end_alone(self) -> None:
        segs = [seg(0, 4, "今天我們討論一個問題。"), seg(5, None, "謝謝觀看。"),
                seg(46, None, "好的，我們繼續。")]
        out, _ = self.run_stage(segs, strip=True)
        self.assertEqual(4.0, out[0]["end"])

    def test_stripping_pins_an_explicit_end_that_ran_past_the_removed_cue(self) -> None:
        # Kept, a declared end past the next cue's start is pulled back to it by build_cues.
        # With that next cue removed, nothing pulls it back, so the survivor would take over
        # the removed cue's time (verify round 1, found twice)
        segs = [seg(0, 100, "今天我們討論一個問題。"), seg(10, 20, "謝謝觀看。"),
                seg(46, 50, "好的，我們繼續。")]
        out, _ = self.run_stage(segs, strip=True)
        self.assertEqual(10.0, out[0]["end"])
        self.assertEqual(46.0, out[1]["start"])

    def test_an_end_that_stops_before_the_removed_cue_is_left_alone(self) -> None:
        segs = [seg(0, 4, "今天我們討論一個問題。"), seg(10, 20, "謝謝觀看。"),
                seg(46, 50, "好的，我們繼續。")]
        out, _ = self.run_stage(segs, strip=True)
        self.assertEqual(4.0, out[0]["end"])

    def test_a_run_of_removed_segments_ends_the_cue_before_at_the_first(self) -> None:
        segs = [seg(0, None, "今天我們討論一個問題。"), seg(5, None, "謝謝觀看。"),
                seg(9, None, "中文。"), seg(46, None, "好的，我們繼續。")]
        out, report = self.run_stage(segs, strip=True)
        self.assertEqual(5.0, out[0]["end"])
        self.assertEqual(2, report.stripped)

    def test_removing_the_first_segment_needs_no_adjustment(self) -> None:
        out, _ = self.run_stage([seg(0, None, "謝謝觀看。"), seg(5, None, "好的，我們繼續。")],
                                strip=True)
        self.assertEqual([5.0], [s["start"] for s in out])

    def test_nothing_flagged_returns_the_same_segments(self) -> None:
        segs = [seg(0, 5, "今天我們討論一個問題。"), seg(5, 9, "好的，我們繼續。")]
        out, report = self.run_stage(segs)
        self.assertEqual(segs, out)
        self.assertEqual(0, report.flagged)


class TestWhatTheRunSays(FlagsCase):
    def report(self, **kw):
        values = dict(total=40, flagged=0, stripped=0, unevaluable=0,
                      by_signal={"low_cps": 0, "ghost_phrase": 0, "repetition": 0})
        values.update(kw)
        return self.flags.Report(**values)

    def test_a_clean_run_says_nothing(self) -> None:
        r = self.report()
        self.assertEqual([], self.flags.summary_lines(r))
        self.assertEqual([], self.flags.ledger_clauses(r))

    def test_flagged_cues_are_counted_by_signal_and_called_kept(self) -> None:
        r = self.report(flagged=4, by_signal={"low_cps": 2, "ghost_phrase": 1, "repetition": 1})
        (line,) = self.flags.summary_lines(r)
        self.assertTrue(line.startswith("⚠ 4 cue(s) flagged"), line)
        for part in ("low_cps: 2", "ghost_phrase: 1", "repetition: 1"):
            self.assertIn(part, line)
        self.assertIn("kept", line)
        self.assertIn("--strip-hallucinations", line)
        # Nothing was lost, so the success line, which a batch caller reads as "what was lost
        # or corrected" (#50), says nothing; the marker is in the file and stderr counts them
        self.assertEqual([], self.flags.ledger_clauses(r))

    def test_a_zero_signal_is_not_listed(self) -> None:
        r = self.report(flagged=1, by_signal={"low_cps": 1, "ghost_phrase": 0, "repetition": 0})
        (line,) = self.flags.summary_lines(r)
        self.assertNotIn("ghost_phrase", line)
        self.assertNotIn("repetition", line)

    def test_removed_cues_are_counted_and_the_ledger_says_so(self) -> None:
        r = self.report(flagged=3, stripped=3, by_signal={"low_cps": 1, "ghost_phrase": 2, "repetition": 0})
        (line,) = self.flags.summary_lines(r)
        self.assertTrue(line.startswith("⚠ 3 cue(s) removed"), line)
        self.assertEqual(["3 cue(s) removed as suspected ASR noise — see stderr"],
                         self.flags.ledger_clauses(r))

    def test_the_cues_that_could_not_be_checked_are_stated(self) -> None:
        r = self.report(unevaluable=7)
        (line,) = self.flags.summary_lines(r)
        self.assertTrue(line.startswith("note:"), line)
        self.assertIn("7 of 40", line)
        self.assertIn("end time", line)
        self.assertEqual([], self.flags.ledger_clauses(r))     # nothing was lost or changed

    def test_a_transcript_with_no_ends_at_all_is_not_commented_on(self) -> None:
        # The point form the MCP path writes. Every clean run would carry the line.
        self.assertEqual([], self.flags.summary_lines(self.report(unevaluable=40)))

    def test_both_lines_when_both_apply(self) -> None:
        r = self.report(flagged=1, unevaluable=2, by_signal={"low_cps": 0, "ghost_phrase": 1, "repetition": 0})
        lines = self.flags.summary_lines(r)
        self.assertEqual(2, len(lines))
        self.assertTrue(lines[0].startswith("⚠"))
        self.assertTrue(lines[1].startswith("note:"))

    def test_no_line_quotes_the_transcript(self) -> None:
        # Counts only. The words are on the cue itself; stderr is read by a model
        r = self.report(flagged=1, by_signal={"low_cps": 1, "ghost_phrase": 0, "repetition": 0})
        for line in self.flags.summary_lines(r):
            self.assertNotRegex(line, r"[「」『』\"']")


if __name__ == "__main__":
    unittest.main()
