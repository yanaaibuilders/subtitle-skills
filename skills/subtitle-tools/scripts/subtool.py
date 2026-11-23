#!/usr/bin/env python3
"""Convert, check, shift, merge and split SRT / WebVTT subtitle files.

Python 3.8+ standard library only. Everything runs on local files; the
script makes no network requests.

    python3 subtool.py check FILE [FILE ...] [--json]
    python3 subtool.py convert IN -o OUT            (.srt / .vtt / .txt)
    python3 subtool.py shift IN --by OFFSET -o OUT [--clamp]
    python3 subtool.py merge IN1 IN2 [IN ...] -o OUT
    python3 subtool.py split IN (--at TIME [--at TIME ...] | --every N) [--out-dir DIR]

Exit codes: 0 = ok, 1 = problems found or operation refused, 2 = bad arguments.
"""

from __future__ import annotations

import argparse
import bisect
import dataclasses
import html
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ERROR = "error"
WARNING = "warning"
INFO = "info"

MAX_SHOWN_PER_CODE = 5


class SubtitleError(Exception):
    """A problem the user has to fix. Printed as one line, no traceback."""


@dataclass
class Issue:
    severity: str
    code: str
    line: int | None
    message: str


@dataclass
class Cue:
    start: int  # 毫秒
    end: int
    text: str
    ident: str | None = None
    settings: str = ""
    line: int = 0  # 时间行所在的行号（从 1 开始）


@dataclass
class Subtitle:
    fmt: str
    cues: list = field(default_factory=list)
    issues: list = field(default_factory=list)
    header: list = field(default_factory=list)
    blocks: list = field(default_factory=list)
    encoding: str = "utf-8"
    bom: bool = False
    newlines: str = "none"

    def add(self, severity, code, line, message):
        self.issues.append(Issue(severity, code, line, message))


# ---------------------------------------------------------------- time values

# 读取时故意宽松（逗号/点、缺小时、毫秒位数不足都能读），再用严格格式单独报告
_TIME = r"(?:(\d+):)?(\d+):(\d+)(?:[.,](\d+))?"
_LOOSE_TIMING = re.compile(r"^\s*" + _TIME + r"\s*-->\s*" + _TIME + r"(.*)$")
_SRT_STRICT = re.compile(
    r"^\s*\d{2,}:\d{2}:\d{2},\d{3}\s+-->\s+\d{2,}:\d{2}:\d{2},\d{3}(?:\s|$)"
)
_VTT_STRICT = re.compile(
    r"^(?:\d{2,}:)?\d{2}:\d{2}\.\d{3}[ \t]+-->[ \t]+(?:\d{2,}:)?\d{2}:\d{2}\.\d{3}(?:[ \t]|$)"
)
_DIGITS = re.compile(r"[0-9]+")


def _to_ms(hours, minutes, seconds, fraction):
    m, s = int(minutes), int(seconds)
    if m > 59 or s > 59:
        return None
    ms = int((fraction or "")[:3].ljust(3, "0"))
    return ((int(hours or 0) * 60 + m) * 60 + s) * 1000 + ms


def format_time(ms, fmt):
    sign = "-" if ms < 0 else ""
    ms = abs(ms)
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1000)
    sep = "," if fmt == "srt" else "."
    return f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}{sep}{millis:03d}"


def format_offset(ms):
    return f"{'+' if ms >= 0 else '-'}{abs(ms) / 1000:.3f}s"


_DURATION = re.compile(
    r"^([+-]?)(?:(\d+)ms|(\d+(?:\.\d+)?)s?|((?:\d+:){1,2}\d+(?:[.,]\d+)?))$"
)


def parse_duration(raw, signed=True):
    """'2.5', '-1.5s', '-500ms', '+00:01:02,500', '1:30' -> milliseconds."""
    value = raw.strip()
    match = _DURATION.match(value)
    hint = 'use e.g. 2.5, -1.5s, -500ms or 00:01:02,500'
    if not match or (match.group(1) and not signed):
        raise SubtitleError(f'cannot read time "{raw}"; {hint}')
    sign = -1 if match.group(1) == "-" else 1
    if match.group(2) is not None:
        return sign * int(match.group(2))
    if match.group(3) is not None:
        whole, _, frac = match.group(3).partition(".")
        numbers = [int(whole)]
    else:
        *front, last = match.group(4).split(":")
        sec, _, frac = last.replace(",", ".").partition(".")
        numbers = [int(x) for x in front] + [int(sec)]
        # 最高位可以超过 59（例如 90:00），其余分、秒必须在 0–59
        if any(n > 59 for n in numbers[1:]):
            raise SubtitleError(f'cannot read time "{raw}": minutes and seconds must be 00-59')
    if len(frac) > 3:
        raise SubtitleError(f'cannot read time "{raw}": use at most 3 decimal places (milliseconds)')
    total = 0
    for n in numbers:
        total = total * 60 + n
    return sign * (total * 1000 + int(frac.ljust(3, "0")))


# ------------------------------------------------------------------- reading

def _decode(data, fmt, sub):
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        sub.encoding, sub.bom = "utf-16", True
        try:
            text = data.decode("utf-16")
        except UnicodeDecodeError:
            sub.encoding = "invalid"
            sub.add(ERROR, "not-utf8", None, "looks like UTF-16 but cannot be decoded")
            return data.decode("utf-16", errors="replace")
        sub.add(WARNING, "utf16", None,
                "UTF-16 encoded; many web players and uploaders expect UTF-8 (convert writes UTF-8)")
        return text.lstrip("﻿")
    body = data
    if data.startswith(b"\xef\xbb\xbf"):
        body, sub.bom = data[3:], True
        if fmt == "vtt":
            sub.add(INFO, "bom", None, "UTF-8 BOM at the start (allowed in WebVTT)")
        else:
            sub.add(WARNING, "bom", None,
                    "UTF-8 BOM at the start; some players and scripts read it as part of the "
                    "first cue number (convert writes UTF-8 without BOM)")
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError as exc:
        sub.encoding = "invalid"
        line = body[:exc.start].count(b"\n") + 1
        sub.add(ERROR, "not-utf8", line,
                f"not valid UTF-8 (byte 0x{body[exc.start]:02x}); re-save the file as UTF-8 "
                "(this tool does not guess other encodings)")
        return body.decode("utf-8", errors="replace")


def _newline_style(text, sub):
    crlf = text.count("\r\n")
    cr = text.count("\r") - crlf
    lf = text.count("\n") - crlf
    kinds = [name for name, n in (("crlf", crlf), ("lf", lf), ("cr", cr)) if n]
    if not kinds:
        sub.newlines = "none"
    elif len(kinds) > 1:
        sub.newlines = "mixed"
        sub.add(WARNING, "mixed-newlines", None,
                f"mixed line endings ({crlf} CRLF, {lf} LF, {cr} CR); some tools split cues wrongly")
    else:
        sub.newlines = kinds[0]
        if kinds[0] == "crlf":
            sub.add(INFO, "crlf", None, "Windows line endings (CRLF); most players accept them, "
                    "convert writes LF")
        elif kinds[0] == "cr":
            sub.add(WARNING, "cr-newlines", None,
                    "old Mac line endings (CR only); many tools read the whole file as one line")


def _read_vtt_header(lines, sub):
    first = lines[0] if lines else ""
    if re.match(r"WEBVTT(?:[ \t]|$)", first):
        sub.header = [first]
        i = 1
        while i < len(lines) and lines[i].strip():
            if "-->" in lines[i]:
                sub.add(ERROR, "header-blank-line", i + 1,
                        "no blank line between the header and the first cue; players may drop that cue")
                return i
            sub.header.append(lines[i])
            i += 1
        return i
    sub.header = ["WEBVTT"]
    if first.startswith("WEBVTT"):
        sub.add(ERROR, "bad-header", 1,
                'the first line must be "WEBVTT" alone or followed by a space and text')
        return 1
    sub.add(ERROR, "missing-header", 1,
            'the first line must be "WEBVTT"; players reject the file without it')
    return 0


def _blocks(lines, start, sub):
    block = []
    for i in range(start, len(lines)):
        line = lines[i]
        if line.strip():
            block.append((i + 1, line))
            continue
        # 浏览器只把真正的空行当分隔；只含空格的行会把前后两段粘成一个 cue
        if (sub.fmt == "vtt" and line and block and i + 1 < len(lines)
                and lines[i + 1].strip()):
            sub.add(WARNING, "space-only-line", i + 1,
                    "line contains only spaces; WebVTT players do not treat it as a blank line")
        if block:
            yield block
            block = []
    if block:
        yield block


_VTT_SETTINGS = {
    "vertical": r"(?:rl|lr)",
    "line": r"(?:-?\d+|\d+(?:\.\d+)?%)(?:,(?:start|center|end))?",
    "position": r"\d+(?:\.\d+)?%(?:,(?:line-left|center|line-right))?",
    "size": r"\d+(?:\.\d+)?%",
    "align": r"(?:start|center|end|left|right)",
    "region": r".+",
}


def _check_settings(raw, lineno, sub):
    seen = set()
    for token in raw.split():
        name, sep, value = token.partition(":")
        if not sep or not name or not value:
            sub.add(WARNING, "bad-setting", lineno,
                    f'"{token}" is not a name:value cue setting; players ignore it')
            continue
        if name not in _VTT_SETTINGS:
            sub.add(WARNING, "unknown-setting", lineno,
                    f'unknown cue setting "{name}"; players ignore it')
            continue
        if name in seen:
            sub.add(WARNING, "duplicate-setting", lineno, f'cue setting "{name}" appears twice')
        seen.add(name)
        too_big = any(float(p) > 100 for p in re.findall(r"(\d+(?:\.\d+)?)%", value))
        if too_big or not re.fullmatch(_VTT_SETTINGS[name], value):
            hint = ' (use "center")' if name == "align" and value == "middle" else ""
            sub.add(WARNING, "bad-setting", lineno,
                    f'invalid value for {name}: "{value}"{hint}; players ignore it')


def _parse_timing(lineno, line, sub):
    match = _LOOSE_TIMING.match(line)
    if not match:
        sub.add(ERROR, "bad-timing", lineno, f'cannot read timing line "{line.strip()}" (cue skipped)')
        return None
    start = _to_ms(*match.group(1, 2, 3, 4))
    end = _to_ms(*match.group(5, 6, 7, 8))
    if start is None or end is None:
        sub.add(ERROR, "time-range", lineno,
                "minutes and seconds must be 00-59 (cue skipped)")
        return None
    rest = match.group(9).strip()
    if sub.fmt == "srt":
        if not _SRT_STRICT.match(line):
            sub.add(WARNING, "nonstandard-time", lineno,
                    "non-standard timestamp; SRT expects HH:MM:SS,mmm --> HH:MM:SS,mmm")
        if rest:
            sub.add(WARNING, "extra-after-time", lineno, f'text after the end time is ignored: "{rest}"')
        return start, end, ""
    if not _VTT_STRICT.match(line):
        sub.add(ERROR, "nonstandard-time", lineno,
                "timestamp is not valid WebVTT ([HH:]MM:SS.mmm with a dot and spaces around -->); "
                "browsers skip this cue")
    elif rest:
        _check_settings(rest, lineno, sub)
    return start, end, rest


def _cues_from_block(block, sub):
    """One blank-line block usually holds one cue; a missing blank line makes it hold several."""
    timing_at = [k for k, (_, line) in enumerate(block) if "-->" in line]
    if not timing_at:
        sub.add(ERROR, "no-timing", block[0][0],
                "text without a timing line (skipped); a broken --> arrow or a blank line inside a cue causes this")
        return []
    first = timing_at[0]
    if first > 1:
        if sub.fmt == "vtt":
            sub.add(ERROR, "no-timing", block[0][0],
                    "the timing line must be the first or second line of a cue; players skip this block")
            return []
        sub.add(ERROR, "no-timing", block[0][0], "text before the cue number (skipped)")
    if len(timing_at) > 1:
        sub.add(ERROR, "missing-blank-line", block[timing_at[1]][0],
                "no blank line before this cue; many players merge it into the previous cue")
    cues = []
    for k, t in enumerate(timing_at):
        nxt = timing_at[k + 1] if k + 1 < len(timing_at) else len(block)
        text_end = nxt
        # SRT 漏空行时，下一条的序号行紧贴在时间行前面，不能算进上一条的文字
        if (sub.fmt == "srt" and nxt < len(block) and nxt - 1 > t
                and _DIGITS.fullmatch(block[nxt - 1][1].strip())):
            text_end = nxt - 1
        if k == 0:
            ident = block[first - 1][1] if first >= 1 else None
        elif sub.fmt == "srt" and t - 1 > timing_at[k - 1] and _DIGITS.fullmatch(block[t - 1][1].strip()):
            ident = block[t - 1][1]
        else:
            ident = None
        lineno, line = block[t]
        parsed = _parse_timing(lineno, line, sub)
        if parsed is None:
            continue
        start, end, settings = parsed
        text = "\n".join(line for _, line in block[t + 1:text_end])
        if not text.strip():
            sub.add(WARNING, "empty-cue", lineno, "cue has no text")
        cues.append(Cue(start, end, text, ident, settings, lineno))
    return cues


def _check_numbers(sub):
    cues = sub.cues
    missing = [c for c in cues if c.ident is None]
    if missing:
        sub.add(WARNING, "missing-number", missing[0].line,
                f"{len(missing)} cue(s) have no number line (first one here); convert adds numbers")
    for c in cues:
        if c.ident is not None and not _DIGITS.fullmatch(c.ident.strip()):
            sub.add(WARNING, "bad-number", c.line - 1, f'cue number "{c.ident.strip()}" is not a whole number')
    wrong = [(pos, c) for pos, c in enumerate(cues, 1)
             if c.ident is not None and _DIGITS.fullmatch(c.ident.strip()) and int(c.ident) != pos]
    if wrong:
        pos, c = wrong[0]
        sub.add(WARNING, "number-sequence", c.line - 1,
                f"cue numbers do not run 1, 2, 3...: expected {pos}, found {c.ident.strip()} "
                f"({len(wrong)} cue(s) affected); convert renumbers them")


def _check_timing(sub):
    t = lambda ms: format_time(ms, sub.fmt)  # noqa: E731
    latest = None
    previous = None
    for c in sub.cues:
        if c.end < c.start:
            sub.add(ERROR, "negative-duration", c.line, f"ends before it starts ({t(c.start)} --> {t(c.end)})")
        elif c.end == c.start:
            sub.add(ERROR, "zero-duration", c.line, f"starts and ends at the same time ({t(c.start)})")
        if previous is not None and c.start < previous.start:
            sub.add(WARNING, "out-of-order", c.line,
                    f"starts at {t(c.start)}, before the previous cue (line {previous.line}, {t(previous.start)})")
        elif latest is not None and c.start < latest.end:
            sub.add(WARNING, "overlap", c.line,
                    f"starts at {t(c.start)}, before the cue at line {latest.line} ends ({t(latest.end)})")
        if latest is None or c.end > latest.end:
            latest = c
        previous = c


def detect_format(path, data):
    head = data[3:] if data.startswith(b"\xef\xbb\xbf") else data
    if head.startswith(b"WEBVTT"):
        return "vtt"
    return "vtt" if Path(path).suffix.lower() == ".vtt" else "srt"


def parse(data, fmt):
    sub = Subtitle(fmt=fmt)
    text = _decode(data, fmt, sub)
    _newline_style(text, sub)
    lines = re.split(r"\r\n|\r|\n", text)
    start = _read_vtt_header(lines, sub) if fmt == "vtt" else 0
    for block in _blocks(lines, start, sub):
        head = block[0][1]
        if fmt == "vtt":
            if re.match(r"NOTE(?:[ \t]|$)", head):
                continue
            if re.match(r"(?:STYLE|REGION)[ \t]*$", head):
                if sub.cues:
                    sub.add(WARNING, "late-style", block[0][0],
                            "STYLE and REGION blocks after the first cue are ignored by players")
                else:
                    sub.blocks.append("\n".join(line for _, line in block))
                continue
        sub.cues.extend(_cues_from_block(block, sub))
    if not sub.cues:
        sub.add(ERROR, "no-cues", None, "no subtitle cues found")
    if fmt == "srt":
        _check_numbers(sub)
    _check_timing(sub)
    return sub


def parse_file(path, data):
    fmt = detect_format(path, data)
    sub = parse(data, fmt)
    if fmt == "vtt" and Path(path).suffix.lower() == ".srt":
        sub.add(WARNING, "format-mismatch", None, "file is named .srt but contains WebVTT; read as WebVTT")
    return sub


def load(path):
    """Read a subtitle file for convert/shift/merge/split."""
    data = Path(path).read_bytes()
    sub = parse_file(path, data)
    if sub.encoding == "invalid":
        raise SubtitleError(f"{path} is not valid UTF-8; re-save it as UTF-8 first")
    if not sub.cues:
        raise SubtitleError(f"{path}: no subtitle cues found")
    errors = sum(1 for i in sub.issues if i.severity == ERROR)
    if errors:
        print(f"note: {path} has {errors} error(s); the output uses the cues that could be read "
              f"(unreadable blocks are skipped). Run: subtool.py check {path}", file=sys.stderr)
    return sub


# ------------------------------------------------------------------- writing

_SRT_TAG = re.compile(r"</?[A-Za-z][^<>\n]*>")
_VTT_TAG = re.compile(r"</?[A-Za-z][^<>\n]*>|<\d[^<>\n]*>")
_BASIC_TAG = re.compile(r"<(/?)([ibu])(?:[.\s][^>]*)?>", re.IGNORECASE)
_OVERRIDE = re.compile(r"\{\\[^{}]*\}")
_VTT_ENTITY = re.compile(r"&(?:amp|lt|gt|nbsp|lrm|rlm|#\d+|#x[0-9A-Fa-f]+);")


def _vtt_unescape(text):
    # 只认 WebVTT 的转义；html.unescape 会把 "&notice" 里的 "&not" 也当成实体
    return _VTT_ENTITY.sub(lambda m: html.unescape(m.group(0)), text)


def _swap_tags(text, tag_re, clean):
    out, pos = [], 0
    for match in tag_re.finditer(text):
        out.append(clean(text[pos:match.start()]))
        basic = _BASIC_TAG.fullmatch(match.group(0))
        if basic:
            out.append(f"<{basic.group(1)}{basic.group(2).lower()}>")
        pos = match.end()
    out.append(clean(text[pos:]))
    return "".join(out)


def _srt_text_to_vtt(text):
    # <i>/<b>/<u> 两种格式都支持；<font> 和 {\an8} 之类 WebVTT 没有，去掉
    return _swap_tags(_OVERRIDE.sub("", text), _SRT_TAG, lambda s: html.escape(s, quote=False))


def _vtt_text_to_srt(text):
    return _swap_tags(text, _VTT_TAG, _vtt_unescape)


def _plain_text(text, fmt):
    if fmt == "srt":
        text = _SRT_TAG.sub("", _OVERRIDE.sub("", text))
    else:
        text = _vtt_unescape(_VTT_TAG.sub("", text))
    joined = " ".join(line.strip() for line in text.split("\n") if line.strip())
    return re.sub(r"[ \t]{2,}", " ", joined)


def retarget(cue, src, dst):
    if dst == "txt":
        text = _plain_text(cue.text, src)
    elif src == dst:
        text = cue.text
    elif dst == "vtt":
        text = _srt_text_to_vtt(cue.text)
    else:
        text = _vtt_text_to_srt(cue.text)
    same_vtt = src == dst == "vtt"
    return dataclasses.replace(cue, text=text, ident=cue.ident if same_vtt else None,
                               settings=cue.settings if same_vtt else "")


def render(cues, fmt, header=None, blocks=()):
    if fmt == "txt":
        lines = [c.text for c in cues if c.text]
        return "\n".join(lines) + "\n" if lines else ""
    parts = []
    if fmt == "vtt":
        parts.append("\n".join(header or ["WEBVTT"]))
        parts.extend(blocks)
    for number, c in enumerate(cues, 1):
        timing = f"{format_time(c.start, fmt)} --> {format_time(c.end, fmt)}"
        if fmt == "vtt" and c.settings:
            timing += " " + c.settings
        if fmt == "srt":
            head = [str(number)]
        else:
            head = [c.ident.strip()] if c.ident and c.ident.strip() else []
        # 文字里的空行会被读成 cue 分隔，写出时去掉
        body = [line for line in c.text.split("\n") if line.strip()]
        parts.append("\n".join(head + [timing] + body))
    return "\n\n".join(parts) + "\n"


def output_format(path):
    ext = Path(path).suffix.lower()
    if ext not in (".srt", ".vtt", ".txt"):
        raise SubtitleError(f"output file must end in .srt, .vtt or .txt: {path}")
    return ext[1:]


def write_text(path, content):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)


def _write_doc(sub, cues, out_path):
    dst = output_format(out_path)
    converted = [retarget(c, sub.fmt, dst) for c in cues]
    keep = sub.fmt == dst == "vtt"
    write_text(out_path, render(converted, dst, sub.header if keep else None,
                                sub.blocks if keep else ()))
    return dst, converted


# ------------------------------------------------------------------ commands

def _report_dict(name, sub):
    return {
        "file": name,
        "format": sub.fmt,
        "cues": len(sub.cues),
        "encoding": sub.encoding,
        "bom": sub.bom,
        "line_endings": sub.newlines,
        "errors": sum(1 for i in sub.issues if i.severity == ERROR),
        "warnings": sum(1 for i in sub.issues if i.severity == WARNING),
        "issues": [dataclasses.asdict(i) for i in _sorted_issues(sub)],
    }


def _sorted_issues(sub):
    return sorted(sub.issues, key=lambda i: i.line or 0)


def _print_report(report):
    enc = {"utf-8": "UTF-8", "utf-16": "UTF-16", "invalid": "not valid UTF-8"}[report["encoding"]]
    bom = " with BOM" if report["bom"] else ""
    endings = {"lf": "LF", "crlf": "CRLF", "cr": "CR", "mixed": "mixed", "none": "no"}[report["line_endings"]]
    print(f"{report['file']}: {report['format'].upper()}, {report['cues']} cue(s), "
          f"{enc}{bom}, {endings} line endings")
    seen = Counter()
    for issue in report["issues"]:
        seen[issue["code"]] += 1
        if seen[issue["code"]] > MAX_SHOWN_PER_CODE:
            continue
        where = f"line {issue['line']}" if issue["line"] else "file"
        print(f"  {issue['severity']:<7} {where:<10} {issue['message']} [{issue['code']}]")
    for code, count in seen.items():
        if count > MAX_SHOWN_PER_CODE:
            print(f"  ... and {count - MAX_SHOWN_PER_CODE} more [{code}] (use --json for all)")
    if report["errors"] or report["warnings"]:
        print(f"  result: {report['errors']} error(s), {report['warnings']} warning(s)")
    else:
        print("  result: no problems found")


def cmd_check(args):
    reports, failed = [], False
    for name in args.files:
        try:
            data = Path(name).read_bytes()
        except OSError as exc:
            reports.append({"file": name, "read_error": exc.strerror or str(exc)})
            failed = True
            continue
        report = _report_dict(name, parse_file(name, data))
        failed = failed or report["errors"] > 0
        reports.append(report)
    if args.json:
        print(json.dumps(reports, indent=2, ensure_ascii=False))
    else:
        for report in reports:
            if "read_error" in report:
                print(f"{report['file']}: cannot read file ({report['read_error']})")
            else:
                _print_report(report)
    return 1 if failed else 0


def cmd_convert(args):
    sub = load(args.input)
    dst, cues = _write_doc(sub, sub.cues, args.output)
    print(f"wrote {args.output} ({len(cues)} cue(s), {dst.upper()})")
    return 0


def _shift_inline_timestamps(text, offset):
    # WebVTT 卡拉 OK 式的 <00:00:01.000> 行内时间也要跟着平移
    def move(match):
        h, m, s, frac = re.fullmatch(_TIME, match.group(1)).groups()
        return "<" + format_time(max(0, _to_ms(h, m, s, frac) + offset), "vtt") + ">"
    return re.sub(r"<((?:\d+:)?\d{2}:[0-5]\d\.\d{3})>", move, text)


def cmd_shift(args):
    offset = parse_duration(args.by)
    sub = load(args.input)
    earliest = min(min(c.start, c.end) for c in sub.cues)
    if earliest + offset < 0 and not args.clamp:
        first = next(c for c in sub.cues if min(c.start, c.end) == earliest)
        raise SubtitleError(
            f"shift refused: the cue at line {first.line} would start at "
            f"{format_time(earliest + offset, sub.fmt)}. The earliest time in the file is "
            f"{format_time(earliest, sub.fmt)}, so the smallest allowed offset is "
            f"{format_offset(-earliest)}. Add --clamp to cut times at 0 instead "
            "(cues that would end at or before 0 are removed). Nothing was written.")
    shifted, clamped, dropped = [], 0, 0
    for c in sub.cues:
        start, end = c.start + offset, c.end + offset
        # 不加 --clamp 时上面已保证没有负时间，这里只处理 --clamp
        if args.clamp and offset < 0 and end <= 0:
            dropped += 1
            continue
        if start < 0:
            start, clamped = 0, clamped + 1
        text = _shift_inline_timestamps(c.text, offset) if sub.fmt == "vtt" else c.text
        shifted.append(dataclasses.replace(c, start=start, end=end, text=text))
    if not shifted:
        raise SubtitleError("shift refused: every cue would end at or before 0. Nothing was written.")
    _write_doc(sub, shifted, args.output)
    print(f"wrote {args.output} ({len(shifted)} cue(s), shifted by {format_offset(offset)})")
    if clamped or dropped:
        print(f"--clamp: {clamped} start time(s) set to 0, {dropped} cue(s) removed "
              "because they would end at or before 0")
    return 0


def cmd_merge(args):
    if len(args.inputs) < 2:
        raise SubtitleError("merge needs at least two input files")
    dst = output_format(args.output)
    merged = []
    for path in args.inputs:
        sub = load(path)
        merged.extend(retarget(c, sub.fmt, dst) for c in sub.cues)
    # sort 是稳定的：同一时间开始的 cue 保持输入文件的先后顺序
    merged.sort(key=lambda c: c.start)
    merged = [dataclasses.replace(c, ident=None) for c in merged]
    write_text(args.output, render(merged, dst))
    print(f"wrote {args.output} ({len(merged)} cue(s) from {len(args.inputs)} files, {dst.upper()})")
    return 0


def cmd_split(args):
    sub = load(args.input)
    t = lambda ms: format_time(ms, sub.fmt)  # noqa: E731
    crossing = 0
    if args.at:
        points = sorted({parse_duration(a, signed=False) for a in args.at})
        parts = [[] for _ in range(len(points) + 1)]
        for c in sub.cues:
            k = bisect.bisect_right(points, c.start)
            parts[k].append(c)
            if k < len(points) and c.end > points[k]:
                crossing += 1
        for i, part in enumerate(parts):
            if part:
                continue
            if i == 0:
                span = f"before {t(points[0])}"
            elif i == len(points):
                span = f"at or after {t(points[-1])}"
            else:
                span = f"between {t(points[i - 1])} and {t(points[i])}"
            raise SubtitleError(f"split refused: part {i + 1} would be empty (no cue starts {span}). "
                                "Nothing was written.")
    else:
        if args.every < 1:
            raise SubtitleError("--every must be 1 or more")
        parts = [sub.cues[i:i + args.every] for i in range(0, len(sub.cues), args.every)]
    out_dir = Path(args.out_dir) if args.out_dir else Path(args.input).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(args.input).stem
    for number, part in enumerate(parts, 1):
        path = out_dir / f"{stem}.part{number}.{sub.fmt}"
        write_text(path, render(part, sub.fmt, sub.header, sub.blocks))
        print(f"wrote {path} ({len(part)} cue(s), {t(part[0].start)} - {t(max(c.end for c in part))})")
    if crossing:
        print(f"{crossing} cue(s) cross a split point and stay whole in the earlier part")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="subtool.py",
        description="Convert, check, shift, merge and split SRT / WebVTT files. Local only.",
        epilog="Exit codes: 0 ok, 1 problems found or operation refused, 2 bad arguments.")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    commands.required = True

    p = commands.add_parser("check", help="report structural, timing and encoding problems")
    p.add_argument("files", nargs="+", metavar="FILE")
    p.add_argument("--json", action="store_true", help="print a JSON report")
    p.set_defaults(func=cmd_check)

    p = commands.add_parser("convert", help="SRT <-> VTT, or SRT/VTT -> plain text")
    p.add_argument("input")
    p.add_argument("-o", "--output", required=True, help="output path ending in .srt, .vtt or .txt")
    p.set_defaults(func=cmd_convert)

    p = commands.add_parser("shift", help="move every cue earlier or later by the same amount")
    p.add_argument("input")
    p.add_argument("--by", required=True, metavar="OFFSET",
                   help="e.g. 2.5, -1.5s, -500ms, +00:00:01,200 (negative = earlier)")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--clamp", action="store_true",
                   help="instead of refusing, set negative start times to 0 and remove cues "
                        "that would end at or before 0")
    p.set_defaults(func=cmd_shift)

    p = commands.add_parser("merge", help="merge files into one, sorted by start time and renumbered")
    p.add_argument("inputs", nargs="+", metavar="INPUT")
    p.add_argument("-o", "--output", required=True)
    p.set_defaults(func=cmd_merge)

    p = commands.add_parser("split", help="split one file at time points or every N cues")
    p.add_argument("input")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--at", action="append", metavar="TIME",
                       help="split point such as 00:10:00 or 600; repeat for more parts")
    group.add_argument("--every", type=int, metavar="N", help="start a new part every N cues")
    p.add_argument("--out-dir", help="folder for NAME.partK.EXT files (default: next to the input)")
    p.set_defaults(func=cmd_split)
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # argparse 会把 "--by -2s" 里的 -2s 当成选项名，这里合并成 "--by=-2s"
    for i in range(len(argv) - 1):
        if argv[i] == "--by" and argv[i + 1].startswith("-"):
            argv[i:i + 2] = [f"--by={argv[i + 1]}"]
            break
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SubtitleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc.filename or ''}: {exc.strerror or exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
