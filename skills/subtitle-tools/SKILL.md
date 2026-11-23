---
name: subtitle-tools
description: Convert, check, shift, merge and split SRT and WebVTT subtitle files on the local machine with one zero-dependency Python 3 script. Use when the user wants SRT to VTT, VTT to SRT, or subtitles to plain text; wants a subtitle file validated (overlapping cues, zero or negative durations, cue numbering, UTF-8 and BOM, CRLF line endings, the WEBVTT header, cue settings); wants all subtitle times moved earlier or later; or wants two subtitle files merged or one file split by time or by cue count. It does not create timing from audio or video, transcribe speech, or translate.
---

# Subtitle tools

One script, `scripts/subtool.py`, next to this file. Python 3.8+ standard library only. It reads and writes local files and makes no network requests.

Run it with the path to this skill folder, for example:

```bash
python3 <this-skill-folder>/scripts/subtool.py check episode.srt
```

## Commands

| Task | Command |
|---|---|
| Validate one or more files | `subtool.py check FILE [FILE ...] [--json]` |
| SRT to VTT, VTT to SRT, either to TXT | `subtool.py convert IN -o OUT.vtt` (format comes from the `-o` extension: `.srt`, `.vtt`, `.txt`) |
| Clean up an SRT (renumber, drop BOM, LF line endings) | `subtool.py convert IN.srt -o OUT.srt` |
| Move every cue later / earlier | `subtool.py shift IN --by 2.5 -o OUT` / `subtool.py shift IN --by=-1.5s -o OUT` |
| Merge files | `subtool.py merge A.srt B.vtt [C ...] -o OUT.srt` |
| Split by time | `subtool.py split IN --at 00:10:00 [--at 00:20:00] [--out-dir DIR]` |
| Split by cue count | `subtool.py split IN --every 200 [--out-dir DIR]` |

Offsets and times accept `2.5`, `2.5s`, `-500ms`, `+00:00:01,200`, `01:30` (minutes:seconds). At most 3 decimal places.

Exit codes: `0` ok; `1` problems found (check) or the operation was refused; `2` bad arguments.

## Rules the script follows

- **check** reports errors (players may drop cues or reject the file) and warnings (usually still plays). Errors: invalid UTF-8, unreadable or out-of-range timestamps, text without a timing line, a missing blank line between cues, zero or negative duration, a missing or malformed `WEBVTT` header, and SRT-style timestamps (comma, missing parts) inside a VTT file. Warnings: overlaps, cues out of order, SRT numbering gaps or missing numbers, non-standard SRT timestamps (such as a dot instead of a comma), a UTF-8 BOM in SRT, mixed or CR-only line endings, UTF-16, unknown or invalid VTT cue settings, empty cues. CRLF alone and a BOM in VTT are informational. Each kind is shown at most 5 times; `--json` lists all.
- **convert / shift / merge / split** read leniently. If the input has errors they still write output from the cues that could be read, skip unreadable blocks, and print a note to stderr. Output is always UTF-8 without BOM and with LF line endings. Invalid UTF-8 input is refused.
- **shift** never writes a negative time. By default it refuses, names the first cue that would go below zero and the smallest allowed offset, and writes nothing. With `--clamp`, start times below 0 become 0 and cues that would end at or before 0 are removed; the counts are printed. VTT inline timestamps (`<00:00:01.000>`) move with the cue.
- **merge** sorts all cues by start time (cues with the same start keep input order) and renumbers. VTT identifiers, NOTE, STYLE and REGION blocks are not carried into merged output; VTT cue settings are kept when the output is VTT.
- **split** keeps original times. With `--at`, a cue belongs to the part in which it starts; a cue that crosses a split point stays whole in the earlier part. If any part would be empty, it refuses and writes nothing. Files are named `NAME.part1.EXT`, `NAME.part2.EXT`, ... and existing files with those names are overwritten. To make a later part start at 0:00, shift it afterwards, e.g. `--by=-00:10:00`.
- **SRT to VTT** keeps `<i>`, `<b>`, `<u>`, removes `<font>` and `{\an8}`-style tags, and escapes `&`, `<`, `>`. **VTT to SRT** keeps `<i>`, `<b>`, `<u>`, removes voice, class, ruby and timestamp tags, decodes `&amp;` `&lt;` `&gt;` `&nbsp;` `&lrm;` `&rlm;` and numeric references, and drops cue settings, identifiers, NOTE, STYLE and REGION. **TXT** gives one line per cue, tags removed, empty cues skipped.
- **VTT to VTT** (convert, shift, split) keeps the header lines, STYLE and REGION blocks, identifiers and cue settings; NOTE blocks are dropped.

## How to use this skill well

1. When the user reports a broken subtitle file, run `check` first and explain the findings in plain words before changing anything.
2. Write results to a new file name unless the user asks to overwrite the original.
3. After writing, run `check` on the output and tell the user the result.
4. If a shift is refused, tell the user the smallest allowed offset and ask whether to use that value or `--clamp`.

## What this skill cannot do

Say so plainly instead of improvising:

- create or fix timing from audio or video, or line subtitles up with speech;
- transcribe speech or translate text;
- stretch or rescale timing (for example a frame-rate change); `shift` moves every cue by the same amount;
- read formats other than SRT and WebVTT (ASS/SSA, SBV, TTML, SCC, ...);
- guess legacy encodings such as GBK, Shift_JIS or Windows-1252; the file must be UTF-8 (UTF-16 with a BOM is read);
- fix overlaps, re-wrap long lines, or burn subtitles into video.
