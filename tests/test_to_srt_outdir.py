#!/usr/bin/env python3
"""`to_srt.py <id> --out-dir DIR` names the file itself, so a recording's name never reaches a shell (#69).

`plaud-to-srt` used to tell the model to run `to_srt.py <id> -o "<name>.srt"`. The name is text from
Plaud, and Plaud writes some names itself, from what was said in the recording. Inside double quotes
it is still expanded by the shell (`$(...)`, backticks), and a `/` in it turns the name into a path.
The only way to keep it off the command line is for the script to read the name from the cache and
build the file name itself.

Each test names a break:

* a name with a separator, `..` or a control character must not produce a path outside `--out-dir`;
* a name that is empty or only dots must not produce `.srt`, a hidden file or the directory itself;
* an existing file must never be overwritten (two recordings can share a name);
* a very long CJK name must not exceed the file system's limit in BYTES;
* `--out-dir` must not combine with `-o`, `--file` or a directory that is not there.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

_spec = importlib.util.spec_from_file_location("to_srt_outdir_under_test", SCRIPTS / "to_srt.py")
to_srt = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(SCRIPTS))
assert _spec.loader is not None
_spec.loader.exec_module(to_srt)

TRANSCRIPT = "[00:00:01.000 - 00:00:03.000] Speaker 1: hello there\n[00:00:04.000 - 00:00:06.000] Speaker 1: goodbye\n"


class TestOutputStem(unittest.TestCase):
    """The file name is built from the name by a pure function, so it can be tested with hostile names."""

    def stem(self, name, rec="of_abc"):
        return to_srt.output_stem(name, rec)

    def test_a_plain_name_is_kept(self):
        self.assertEqual("Weekly sync", self.stem("Weekly sync"))

    def test_cjk_is_kept(self):
        self.assertEqual("10-14 課程講座", self.stem("10-14 課程講座"))

    def test_separators_never_survive(self):
        for name in ("a/b", "a\\b", "../../etc/passwd", "/abs/path", "a:b"):
            with self.subTest(name=name):
                out = self.stem(name)
                self.assertFalse(set(out) & set("/\\:"), out)

    def test_dot_dot_cannot_name_the_parent(self):
        for name in ("..", ".", "...", " . ", ". ."):
            with self.subTest(name=name):
                self.assertEqual("of_abc", self.stem(name))

    def test_leading_dots_are_not_kept_so_the_file_is_not_hidden(self):
        self.assertFalse(self.stem(".hidden").startswith("."))

    def test_control_characters_do_not_survive(self):
        out = self.stem("a\nb\x1b[2Kc\x00d")
        self.assertFalse([c for c in out if ord(c) < 32 or ord(c) == 127], repr(out))

    def test_an_empty_name_falls_back_to_the_id(self):
        for name in ("", "   ", None):
            with self.subTest(name=name):
                self.assertEqual("of_abc", self.stem(name))

    def test_a_shell_substitution_is_just_characters_here(self):
        # The name never reaches a shell any more; it must also be harmless as a file name.
        out = self.stem("$(touch pwned)`id`")
        self.assertNotIn("/", out)
        self.assertTrue(out)

    def test_length_is_capped_in_bytes_not_characters(self):
        out = self.stem("測" * 200)
        self.assertLessEqual(len(out.encode("utf-8")), 200)
        self.assertTrue(out)

    def test_the_same_text_in_the_other_unicode_form_gives_the_same_stem(self):
        import unicodedata
        nfc = "café"
        self.assertEqual(self.stem(nfc), self.stem(unicodedata.normalize("NFD", nfc)))


class OutDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="plaud-outdir-test-")
        self.addCleanup(self._tmp.cleanup)
        root = pathlib.Path(self._tmp.name)
        self.cache = root / "cache"
        self.out = root / "out"
        self.out.mkdir()
        self.env = {**os.environ, "PLAUD_CACHE_DIR": str(self.cache), "PLAUD_CONFIG_DIR": str(root / "cfg")}

    def put(self, rec: str, name: str) -> None:
        done = subprocess.run(
            [sys.executable, str(SCRIPTS / "cache.py"), "put", "--id", rec, f"--name={name}",
             "--created-at=2026-10-01T00:00:00Z", "--duration=60000"],
            input=TRANSCRIPT, text=True, capture_output=True, env=self.env)
        self.assertEqual(0, done.returncode, done.stderr)

    def run_srt(self, *args):
        return subprocess.run([sys.executable, str(SCRIPTS / "to_srt.py"), *args],
                              text=True, capture_output=True, env=self.env)

    def files(self):
        return sorted(p.name for p in self.out.iterdir())


class TestOutDir(OutDirCase):
    def test_writes_a_file_named_after_the_recording(self):
        self.put("of_aaa", "Weekly sync")
        done = self.run_srt("of_aaa", "--out-dir", str(self.out))
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(["Weekly sync.srt"], self.files())
        self.assertIn("hello there", (self.out / "Weekly sync.srt").read_text(encoding="utf-8"))
        self.assertIn("Weekly sync.srt", done.stdout)

    def test_a_name_with_a_separator_stays_inside_the_directory(self):
        self.put("of_bbb", "a/../../b")
        done = self.run_srt("of_bbb", "--out-dir", str(self.out))
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertEqual(1, len(self.files()))
        self.assertTrue(all(p.parent == self.out for p in self.out.iterdir()))
        self.assertFalse((self.out.parent / "b.srt").exists())

    def test_an_existing_file_is_never_overwritten(self):
        self.put("of_ccc", "Same name")
        self.put("of_ddd", "Same name")
        (self.out / "Same name.srt").write_text("DO NOT TOUCH")
        for rec in ("of_ccc", "of_ddd"):
            self.assertEqual(0, self.run_srt(rec, "--out-dir", str(self.out)).returncode)
        self.assertEqual("DO NOT TOUCH", (self.out / "Same name.srt").read_text())
        self.assertEqual(["Same name-2.srt", "Same name-3.srt", "Same name.srt"], self.files())

    def test_an_option_like_name_is_just_a_file_name(self):
        self.put("of_eee", "-rf")
        self.assertEqual(0, self.run_srt("of_eee", "--out-dir", str(self.out)).returncode)
        self.assertEqual(["-rf.srt"], self.files())

    def test_a_recording_with_no_usable_name_is_named_by_its_id(self):
        self.put("of_fff", "...")
        self.assertEqual(0, self.run_srt("of_fff", "--out-dir", str(self.out)).returncode)
        self.assertEqual(["of_fff.srt"], self.files())

    def test_a_long_cjk_name_fits_the_file_system(self):
        self.put("of_ggg", "測" * 200)
        done = self.run_srt("of_ggg", "--out-dir", str(self.out))
        self.assertEqual(0, done.returncode, done.stderr)
        (name,) = self.files()
        self.assertLessEqual(len(name.encode("utf-8")), 255)

    def test_a_directory_that_is_not_there_is_refused_and_nothing_is_written(self):
        self.put("of_hhh", "x")
        missing = self.out / "nope"
        done = self.run_srt("of_hhh", "--out-dir", str(missing))
        self.assertNotEqual(0, done.returncode)
        self.assertFalse(missing.exists())

    def test_out_dir_and_output_cannot_be_combined(self):
        self.put("of_iii", "x")
        done = self.run_srt("of_iii", "--out-dir", str(self.out), "-o", str(self.out / "y.srt"))
        self.assertNotEqual(0, done.returncode)
        self.assertEqual([], self.files())

    def test_out_dir_needs_a_recording_id_not_a_file(self):
        f = pathlib.Path(self._tmp.name) / "t.md"
        f.write_text("---\nid: x\n---\n" + TRANSCRIPT)
        done = self.run_srt("--file", str(f), "--out-dir", str(self.out))
        self.assertNotEqual(0, done.returncode)
        self.assertEqual([], self.files())

    def test_dash_o_still_works_as_before(self):
        self.put("of_jjj", "x")
        target = self.out / "chosen.srt"
        self.assertEqual(0, self.run_srt("of_jjj", "-o", str(target)).returncode)
        self.assertTrue(target.is_file())


if __name__ == "__main__":
    unittest.main()
