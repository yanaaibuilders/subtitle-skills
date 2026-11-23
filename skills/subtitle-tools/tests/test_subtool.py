"""Tests for subtool.py. Run: python3 -m unittest discover -s skills/subtitle-tools/tests -v"""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "subtool.py"
SAMPLES = HERE.parent / "samples"
sys.path.insert(0, str(SCRIPT.parent))

import subtool  # noqa: E402


def codes(sub):
    return [issue.code for issue in sub.issues]


def srt(text):
    return subtool.parse(text.encode("utf-8"), "srt")


def vtt(text):
    return subtool.parse(text.encode("utf-8"), "vtt")


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = subtool.main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, content):
        path = self.dir / name
        if isinstance(content, str):
            content = content.encode("utf-8")
        path.write_bytes(content)
        return path


class TimeValues(unittest.TestCase):
    def test_format_time(self):
        self.assertEqual(subtool.format_time(3_723_045, "srt"), "01:02:03,045")
        self.assertEqual(subtool.format_time(3_723_045, "vtt"), "01:02:03.045")
        self.assertEqual(subtool.format_time(0, "srt"), "00:00:00,000")
        self.assertEqual(subtool.format_time(100 * 3_600_000, "vtt"), "100:00:00.000")

    def test_parse_duration_forms(self):
        cases = {
            "2.5": 2500, "+2.5s": 2500, "-1.5s": -1500, "-500ms": -500, "250ms": 250,
            "0": 0, "-00:00:01,200": -1200, "00:01:02.5": 62500, "1:30": 90000,
            "90:00": 5_400_000, "1:02:03.045": 3_723_045,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(subtool.parse_duration(raw), expected)

    def test_parse_duration_rejects(self):
        for raw in ["abc", "1.2345", "1:75", "--1", "1,5", "", "5m"]:
            with self.subTest(raw=raw), self.assertRaises(subtool.SubtitleError):
                subtool.parse_duration(raw)
        with self.assertRaises(subtool.SubtitleError):
            subtool.parse_duration("-5", signed=False)


class SrtReading(unittest.TestCase):
    def test_clean_file(self):
        sub = srt("1\n00:00:01,000 --> 00:00:02,500\nHello\nthere\n\n2\n00:00:03,000 --> 00:00:04,000\nBye\n")
        self.assertEqual(codes(sub), [])
        self.assertEqual([(c.start, c.end) for c in sub.cues], [(1000, 2500), (3000, 4000)])
        self.assertEqual(sub.cues[0].text, "Hello\nthere")

    def test_missing_blank_line_splits_cues_and_keeps_number_out_of_text(self):
        sub = srt("1\n00:00:01,000 --> 00:00:02,000\nOne\n2\n00:00:03,000 --> 00:00:04,000\nTwo\n")
        self.assertIn("missing-blank-line", codes(sub))
        self.assertEqual([c.text for c in sub.cues], ["One", "Two"])
        self.assertEqual([c.ident for c in sub.cues], ["1", "2"])

    def test_stray_text_block(self):
        sub = srt("1\n00:00:01,000 --> 00:00:02,000\nOne\n\nlost line\n\n2\n00:00:03,000 --> 00:00:04,000\nTwo\n")
        self.assertIn("no-timing", codes(sub))
        self.assertEqual(len(sub.cues), 2)

    def test_numbering_problems(self):
        sub = srt("00:00:01,000 --> 00:00:02,000\nA\n\nx\n00:00:03,000 --> 00:00:04,000\nB\n\n"
                  "7\n00:00:05,000 --> 00:00:06,000\nC\n")
        self.assertIn("missing-number", codes(sub))
        self.assertIn("bad-number", codes(sub))
        self.assertIn("number-sequence", codes(sub))
        self.assertTrue(all(i.severity == "warning" for i in sub.issues))

    def test_lenient_timestamps_are_read_with_warning(self):
        sub = srt("1\n0:00:01.5 --> 00:00:02,000 X1:10 X2:20\nA\n")
        self.assertEqual((sub.cues[0].start, sub.cues[0].end), (1500, 2000))
        self.assertIn("nonstandard-time", codes(sub))
        self.assertIn("extra-after-time", codes(sub))

    def test_out_of_range_and_unreadable_timing(self):
        sub = srt("1\n00:61:00,000 --> 00:62:00,000\nA\n\n2\n00:00:0x,000 --> 00:00:02,000\nB\n")
        self.assertEqual(sub.cues, [])
        self.assertIn("time-range", codes(sub))
        self.assertIn("bad-timing", codes(sub))
        self.assertIn("no-cues", codes(sub))

    def test_timing_errors_and_overlap(self):
        sub = srt("1\n00:00:01,000 --> 00:00:05,000\nA\n\n2\n00:00:02,000 --> 00:00:03,000\nB\n\n"
                  "3\n00:00:06,000 --> 00:00:06,000\nC\n\n4\n00:00:08,000 --> 00:00:07,000\nD\n\n"
                  "5\n00:00:04,000 --> 00:00:04,500\nE\n")
        found = {(i.code, i.line) for i in sub.issues}
        self.assertIn(("overlap", 6), found)
        self.assertIn(("zero-duration", 10), found)
        self.assertIn(("negative-duration", 14), found)
        self.assertIn(("out-of-order", 18), found)
        self.assertNotIn(("overlap", 18), found)

    def test_touching_cues_do_not_overlap(self):
        sub = srt("1\n00:00:01,000 --> 00:00:02,000\nA\n\n2\n00:00:02,000 --> 00:00:03,000\nB\n")
        self.assertEqual(codes(sub), [])

    def test_empty_cue_and_empty_file(self):
        self.assertIn("empty-cue", codes(srt("1\n00:00:01,000 --> 00:00:02,000\n")))
        self.assertEqual(codes(srt("")), ["no-cues"])


class EncodingAndNewlines(unittest.TestCase):
    def test_bom_and_crlf(self):
        sub = subtool.parse(b"\xef\xbb\xbf1\r\n00:00:01,000 --> 00:00:02,000\r\nA\r\n", "srt")
        self.assertTrue(sub.bom)
        self.assertEqual(sub.newlines, "crlf")
        self.assertEqual(sub.cues[0].ident, "1")
        by_code = {i.code: i.severity for i in sub.issues}
        self.assertEqual(by_code, {"bom": "warning", "crlf": "info"})

    def test_bom_is_info_for_vtt(self):
        sub = subtool.parse(b"\xef\xbb\xbfWEBVTT\n\n00:01.000 --> 00:02.000\nA\n", "vtt")
        self.assertEqual({i.code: i.severity for i in sub.issues}, {"bom": "info"})

    def test_mixed_and_cr_newlines(self):
        mixed = subtool.parse(b"1\r\n00:00:01,000 --> 00:00:02,000\nA\n", "srt")
        self.assertEqual(mixed.newlines, "mixed")
        cr = subtool.parse(b"1\r00:00:01,000 --> 00:00:02,000\rA\r", "srt")
        self.assertEqual(cr.newlines, "cr")
        self.assertEqual(len(cr.cues), 1)

    def test_invalid_utf8(self):
        sub = subtool.parse(b"1\n00:00:01,000 --> 00:00:02,000\ncaf\xe9\n", "srt")
        self.assertEqual(sub.encoding, "invalid")
        issue = next(i for i in sub.issues if i.code == "not-utf8")
        self.assertEqual((issue.severity, issue.line), ("error", 3))

    def test_utf16_is_decoded_with_warning(self):
        data = "1\n00:00:01,000 --> 00:00:02,000\nHé\n".encode("utf-16")
        sub = subtool.parse(data, "srt")
        self.assertEqual(sub.encoding, "utf-16")
        self.assertEqual(sub.cues[0].text, "Hé")
        self.assertIn("utf16", codes(sub))


class VttReading(unittest.TestCase):
    def test_header_identifiers_settings_and_skipped_blocks(self):
        sub = vtt("WEBVTT - demo\nKind: captions\n\nNOTE a comment\n\nSTYLE\n::cue { color: red }\n\n"
                  "first\n00:01.000 --> 00:02.000 align:start line:0\nA\n\n01:00:03.000 --> 01:00:04.000\nB\n")
        self.assertEqual(codes(sub), [])
        self.assertEqual(sub.header, ["WEBVTT - demo", "Kind: captions"])
        self.assertEqual(sub.blocks, ["STYLE\n::cue { color: red }"])
        self.assertEqual([c.ident for c in sub.cues], ["first", None])
        self.assertEqual(sub.cues[0].settings, "align:start line:0")
        self.assertEqual(sub.cues[1].start, 3_603_000)

    def test_header_problems(self):
        self.assertIn("missing-header", codes(vtt("00:01.000 --> 00:02.000\nA\n")))
        self.assertIn("missing-header", codes(vtt("\nWEBVTT\n\n00:01.000 --> 00:02.000\nA\n")))
        self.assertIn("bad-header", codes(vtt("WEBVTTX\n\n00:01.000 --> 00:02.000\nA\n")))
        no_gap = vtt("WEBVTT\n00:01.000 --> 00:02.000\nA\n")
        self.assertIn("header-blank-line", codes(no_gap))
        self.assertEqual(len(no_gap.cues), 1)

    def test_srt_style_timestamp_is_an_error_in_vtt(self):
        sub = vtt("WEBVTT\n\n00:00:01,000 --> 00:00:02,000\nA\n\n00:03.000-->00:04.000\nB\n")
        errors = [i for i in sub.issues if i.code == "nonstandard-time"]
        self.assertEqual([i.severity for i in errors], ["error", "error"])
        self.assertEqual(len(sub.cues), 2)

    def test_cue_settings(self):
        sub = vtt("WEBVTT\n\n00:01.000 --> 00:02.000 align:middle position:120% size:50% "
                  "foo:1 line:abc vertical:rl vertical:lr bare\nA\n")
        messages = [(i.code, i.message) for i in sub.issues]
        self.assertTrue(any(c == "bad-setting" and "middle" in m and "center" in m for c, m in messages))
        self.assertTrue(any(c == "bad-setting" and "120%" in m for c, m in messages))
        self.assertTrue(any(c == "bad-setting" and "abc" in m for c, m in messages))
        self.assertTrue(any(c == "bad-setting" and '"bare"' in m for c, m in messages))
        self.assertIn("unknown-setting", codes(sub))
        self.assertIn("duplicate-setting", codes(sub))
        self.assertTrue(all(i.severity == "warning" for i in sub.issues))
        good = vtt("WEBVTT\n\n00:01.000 --> 00:02.000 line:-1 position:10%,line-left size:80.5% "
                   "align:end region:top vertical:lr\nA\n")
        self.assertEqual(codes(good), [])

    def test_timing_line_inside_payload_starts_a_new_cue(self):
        sub = vtt("WEBVTT\n\n00:01.000 --> 00:02.000\nA\n00:03.000 --> 00:04.000\nB\n")
        self.assertIn("missing-blank-line", codes(sub))
        self.assertEqual([c.text for c in sub.cues], ["A", "B"])

    def test_timing_on_third_line_drops_block(self):
        sub = vtt("WEBVTT\n\nid\nextra\n00:01.000 --> 00:02.000\nA\n\n00:03.000 --> 00:04.000\nB\n")
        self.assertIn("no-timing", codes(sub))
        self.assertEqual([c.text for c in sub.cues], ["B"])

    def test_space_only_line_warning(self):
        sub = vtt("WEBVTT\n\n00:01.000 --> 00:02.000\nA\n  \n00:03.000 --> 00:04.000\nB\n")
        self.assertIn("space-only-line", codes(sub))

    def test_late_style_block(self):
        sub = vtt("WEBVTT\n\n00:01.000 --> 00:02.000\nA\n\nSTYLE\n::cue { }\n")
        self.assertIn("late-style", codes(sub))
        self.assertEqual(sub.blocks, [])


class TextConversion(unittest.TestCase):
    def test_srt_to_vtt_text(self):
        convert = subtool._srt_text_to_vtt
        self.assertEqual(convert("<I>Hi</I> & <font color=\"red\">you</font>"), "<i>Hi</i> &amp; you")
        self.assertEqual(convert("{\\an8}Top"), "Top")
        self.assertEqual(convert("a < b --> c"), "a &lt; b --&gt; c")
        self.assertEqual(convert("I <3 you"), "I &lt;3 you")

    def test_vtt_to_srt_text(self):
        convert = subtool._vtt_text_to_srt
        self.assertEqual(convert("<v Ann>Hi</v> <i.loud>there</i> &amp; &lt;3"), "Hi <i>there</i> & <3")
        self.assertEqual(convert("<00:00:01.000><c>word</c>"), "word")
        self.assertEqual(convert("I &notice"), "I &notice")

    def test_plain_text(self):
        self.assertEqual(subtool._plain_text("<i>Two</i>\n  lines  here ", "srt"), "Two lines here")
        self.assertEqual(subtool._plain_text("{\\an8}<b>Top</b>", "srt"), "Top")
        self.assertEqual(subtool._plain_text("<v A>R&amp;D</v>", "vtt"), "R&D")


class Commands(TempDirCase):
    SRT = "3\n00:00:01,000 --> 00:00:02,000\n<i>One</i>\n\n9\n00:00:03,000 --> 00:00:04,000\nTwo & two\n"

    def test_check_exit_codes_and_json(self):
        good = self.write("good.srt", self.SRT)
        bad = self.write("bad.srt", "1\n00:00:02,000 --> 00:00:01,000\nA\n")
        code, out, _ = run("check", good)
        self.assertEqual(code, 0)
        self.assertIn("number-sequence", out)
        code, out, _ = run("check", good, bad, "--json")
        self.assertEqual(code, 1)
        reports = json.loads(out)
        self.assertEqual([r["errors"] for r in reports], [0, 1])
        self.assertEqual(reports[1]["issues"][0]["code"], "negative-duration")
        code, out, _ = run("check", self.dir / "missing.srt")
        self.assertEqual(code, 1)
        self.assertIn("cannot read", out)

    def test_check_caps_repeated_issues(self):
        cues = "".join(f"{n}\n00:00:{n:02d},000 --> 00:00:{n:02d},000\nx\n\n" for n in range(1, 9))
        code, out, _ = run("check", self.write("many.srt", cues))
        self.assertEqual(code, 1)
        self.assertEqual(out.count("[zero-duration]"), 6)
        self.assertIn("... and 3 more [zero-duration]", out)

    def test_check_srt_named_file_with_vtt_content(self):
        path = self.write("wrong.srt", "WEBVTT\n\n00:01.000 --> 00:02.000\nA\n")
        code, out, _ = run("check", path)
        self.assertEqual(code, 0)
        self.assertIn("format-mismatch", out)

    def test_convert_srt_to_vtt_and_back(self):
        src = self.write("in.srt", self.SRT)
        code, _, _ = run("convert", src, "-o", self.dir / "out.vtt")
        self.assertEqual(code, 0)
        self.assertEqual((self.dir / "out.vtt").read_text(encoding="utf-8"),
                         "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<i>One</i>\n\n"
                         "00:00:03.000 --> 00:00:04.000\nTwo &amp; two\n")
        run("convert", self.dir / "out.vtt", "-o", self.dir / "back.srt")
        self.assertEqual((self.dir / "back.srt").read_text(encoding="utf-8"),
                         "1\n00:00:01,000 --> 00:00:02,000\n<i>One</i>\n\n"
                         "2\n00:00:03,000 --> 00:00:04,000\nTwo & two\n")

    def test_convert_normalizes_srt_in_place(self):
        path = self.write("x.srt", b"\xef\xbb\xbf5\r\n00:00:01,000 --> 00:00:02,000\r\nA\r\n")
        code, _, _ = run("convert", path, "-o", path)
        self.assertEqual(code, 0)
        self.assertEqual(path.read_bytes(), b"1\n00:00:01,000 --> 00:00:02,000\nA\n")

    def test_convert_vtt_to_vtt_keeps_header_style_ids_settings(self):
        text = ("WEBVTT - t\n\nNOTE gone\n\nSTYLE\n::cue { }\n\nid1\n00:01.000 --> 00:02.000 align:start\n"
                "<v A>Hi</v>\n")
        src = self.write("in.vtt", text)
        run("convert", src, "-o", self.dir / "out.vtt")
        self.assertEqual((self.dir / "out.vtt").read_text(encoding="utf-8"),
                         "WEBVTT - t\n\nSTYLE\n::cue { }\n\nid1\n00:00:01.000 --> 00:00:02.000 align:start\n"
                         "<v A>Hi</v>\n")

    def test_convert_to_txt(self):
        src = self.write("in.srt", self.SRT + "\n10\n00:00:05,000 --> 00:00:06,000\n\n")
        run("convert", src, "-o", self.dir / "out.txt")
        self.assertEqual((self.dir / "out.txt").read_text(encoding="utf-8"), "One\nTwo & two\n")

    def test_convert_refuses_bad_input_and_bad_output_name(self):
        bad = self.write("bad.srt", b"1\n00:00:01,000 --> 00:00:02,000\n\xff\n")
        code, _, err = run("convert", bad, "-o", self.dir / "o.vtt")
        self.assertEqual(code, 1)
        self.assertIn("not valid UTF-8", err)
        self.assertFalse((self.dir / "o.vtt").exists())
        code, _, err = run("convert", self.write("ok.srt", self.SRT), "-o", self.dir / "o.ass")
        self.assertEqual(code, 1)
        self.assertIn(".srt, .vtt or .txt", err)
        code, _, err = run("convert", self.write("empty.srt", "\n\n"), "-o", self.dir / "o.srt")
        self.assertEqual(code, 1)
        self.assertIn("no subtitle cues", err)

    def test_convert_notes_errors_in_input(self):
        src = self.write("in.srt", "1\n00:00:01,000 --> 00:00:02,000\nA\n\nstray\n")
        code, _, err = run("convert", src, "-o", self.dir / "o.srt")
        self.assertEqual(code, 0)
        self.assertIn("1 error(s)", err)

    def test_shift_later_and_earlier(self):
        src = self.write("in.srt", self.SRT)
        code, out, _ = run("shift", src, "--by", "1.5", "-o", self.dir / "late.srt")
        self.assertEqual(code, 0)
        self.assertIn("+1.500s", out)
        self.assertIn("00:00:02,500 --> 00:00:03,500", (self.dir / "late.srt").read_text(encoding="utf-8"))
        code, _, _ = run("shift", src, "--by", "-1s", "-o", self.dir / "early.srt")
        self.assertEqual(code, 0)
        self.assertTrue((self.dir / "early.srt").read_text(encoding="utf-8").startswith(
            "1\n00:00:00,000 --> 00:00:01,000\n"))

    def test_shift_refuses_negative_times_and_writes_nothing(self):
        src = self.write("in.srt", self.SRT)
        out = self.dir / "o.srt"
        code, _, err = run("shift", src, "--by=-1001ms", "-o", out)
        self.assertEqual(code, 1)
        self.assertIn("smallest allowed offset is -1.000s", err)
        self.assertFalse(out.exists())

    def test_shift_negative_duration_cue_cannot_produce_negative_end(self):
        src = self.write("in.srt", "1\n00:00:02,000 --> 00:00:01,000\nA\n")
        code, _, err = run("shift", src, "--by", "-1.5", "-o", self.dir / "o.srt")
        self.assertEqual(code, 1)
        self.assertIn("refused", err)

    def test_shift_clamp(self):
        src = self.write("in.srt", self.SRT + "\n11\n00:00:05,000 --> 00:00:06,000\nThree\n")
        code, out, _ = run("shift", src, "--by", "-3.5s", "--clamp", "-o", self.dir / "o.srt")
        self.assertEqual(code, 0)
        self.assertIn("1 start time(s) set to 0, 1 cue(s) removed", out)
        self.assertEqual((self.dir / "o.srt").read_text(encoding="utf-8"),
                         "1\n00:00:00,000 --> 00:00:00,500\nTwo & two\n\n"
                         "2\n00:00:01,500 --> 00:00:02,500\nThree\n")
        code, _, err = run("shift", src, "--by", "-10", "--clamp", "-o", self.dir / "none.srt")
        self.assertEqual(code, 1)
        self.assertIn("every cue would end at or before 0", err)

    def test_shift_without_clamp_keeps_zero_length_cue_at_zero(self):
        src = self.write("in.srt", "1\n00:00:01,000 --> 00:00:01,000\nA\n")
        code, _, _ = run("shift", src, "--by", "-1", "-o", self.dir / "o.srt")
        self.assertEqual(code, 0)
        self.assertIn("00:00:00,000 --> 00:00:00,000", (self.dir / "o.srt").read_text(encoding="utf-8"))

    def test_shift_vtt_moves_inline_timestamps(self):
        src = self.write("in.vtt", "WEBVTT\n\n00:01.000 --> 00:03.000\n<00:01.000>a <00:02.000>b\n")
        run("shift", src, "--by", "+1:00", "-o", self.dir / "o.vtt")
        self.assertIn("00:01:01.000 --> 00:01:03.000\n<00:01:01.000>a <00:01:02.000>b",
                      (self.dir / "o.vtt").read_text(encoding="utf-8"))

    def test_merge_sorts_renumbers_and_mixes_formats(self):
        a = self.write("a.srt", "1\n00:00:05,000 --> 00:00:06,000\nA2\n\n2\n00:00:01,000 --> 00:00:02,000\nA1\n")
        b = self.write("b.vtt", "WEBVTT\n\nid\n00:03.000 --> 00:04.000\nB &amp; C\n\n00:05.000 --> 00:05.500\nB2\n")
        code, out, _ = run("merge", a, b, "-o", self.dir / "m.srt")
        self.assertEqual(code, 0)
        self.assertIn("4 cue(s) from 2 files", out)
        self.assertEqual((self.dir / "m.srt").read_text(encoding="utf-8"),
                         "1\n00:00:01,000 --> 00:00:02,000\nA1\n\n2\n00:00:03,000 --> 00:00:04,000\nB & C\n\n"
                         "3\n00:00:05,000 --> 00:00:06,000\nA2\n\n4\n00:00:05,000 --> 00:00:05,500\nB2\n")
        run("merge", a, b, "-o", self.dir / "m.vtt")
        merged_vtt = (self.dir / "m.vtt").read_text(encoding="utf-8")
        self.assertNotIn("id\n", merged_vtt)
        self.assertIn("B &amp; C", merged_vtt)

    def test_merge_needs_two_files(self):
        code, _, err = run("merge", self.write("a.srt", self.SRT), "-o", self.dir / "m.srt")
        self.assertEqual(code, 1)
        self.assertIn("at least two", err)

    def test_split_at_times(self):
        cues = "".join(f"{n}\n00:00:{n * 2:02d},000 --> 00:00:{n * 2 + 1:02d},500\nC{n}\n\n" for n in range(1, 7))
        src = self.write("talk.srt", cues)
        code, out, _ = run("split", src, "--at", "00:00:06", "--at", "9", "--out-dir", self.dir / "parts")
        self.assertEqual(code, 0)
        part1 = (self.dir / "parts" / "talk.part1.srt").read_text(encoding="utf-8")
        part2 = (self.dir / "parts" / "talk.part2.srt").read_text(encoding="utf-8")
        part3 = (self.dir / "parts" / "talk.part3.srt").read_text(encoding="utf-8")
        self.assertIn("C2", part1)
        self.assertNotIn("C3", part1)
        self.assertTrue(part2.startswith("1\n00:00:06,000 --> 00:00:07,500\nC3\n"))
        self.assertIn("C4", part2)
        self.assertTrue(part3.startswith("1\n00:00:10,000"))
        self.assertIn("1 cue(s) cross a split point", out)

    def test_split_refuses_empty_part(self):
        src = self.write("talk.srt", self.SRT)
        code, _, err = run("split", src, "--at", "00:10:00")
        self.assertEqual(code, 1)
        self.assertIn("part 2 would be empty", err)
        self.assertFalse((self.dir / "talk.part1.srt").exists())

    def test_split_every_and_vtt_header(self):
        src = self.write("t.vtt", "WEBVTT - x\n\n" + "".join(
            f"c{n}\n00:0{n}.000 --> 00:0{n}.500\nT{n}\n\n" for n in range(1, 6)))
        code, _, _ = run("split", src, "--every", "2")
        self.assertEqual(code, 0)
        names = sorted(p.name for p in self.dir.glob("t.part*.vtt"))
        self.assertEqual(names, ["t.part1.vtt", "t.part2.vtt", "t.part3.vtt"])
        self.assertEqual((self.dir / "t.part3.vtt").read_text(encoding="utf-8"),
                         "WEBVTT - x\n\nc5\n00:00:05.000 --> 00:00:05.500\nT5\n")
        code, _, err = run("split", src, "--every", "0")
        self.assertEqual(code, 1)


class Samples(TempDirCase):
    def test_demo_samples_are_clean(self):
        for name in ["demo.srt", "demo.vtt"]:
            with self.subTest(name=name):
                self.assertEqual(run("check", SAMPLES / name)[0], 0)

    def test_broken_sample_shows_each_problem(self):
        sub = subtool.parse_file("broken.srt", (SAMPLES / "broken.srt").read_bytes())
        for code in ["bom", "crlf", "overlap", "zero-duration", "negative-duration",
                     "number-sequence", "missing-blank-line", "no-timing"]:
            with self.subTest(code=code):
                self.assertIn(code, codes(sub))

    def test_script_runs_as_a_program(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "check", str(SAMPLES / "demo.srt")],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no problems found", result.stdout)
        result = subprocess.run([sys.executable, str(SCRIPT), "shift", "x.srt"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
