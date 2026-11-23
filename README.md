# subtitle-skills

Free subtitle file tools packaged as an Agent Skill for Claude Code and Codex. Convert SRT and WebVTT, export plain text, check a file for common problems, shift all timing, merge and split. It is one Python 3 script that uses only the standard library, works on local files and makes no network requests.

[中文说明](README.zh-CN.md)

## What each tool does and does not do

| Tool | Does | Does not |
|---|---|---|
| `convert` | SRT → VTT, VTT → SRT, SRT or VTT → TXT. SRT → SRT renumbers cues, removes the BOM and writes LF line endings. Keeps `<i>` `<b>` `<u>`. | Read or write ASS/SSA, SBV, TTML or other formats. Keep VTT positioning (cue settings) when the output is SRT, because SRT has no equivalent. |
| `check` | Reports overlapping cues, zero or negative durations, cues out of order, SRT numbering, invalid UTF-8, BOM, UTF-16, CRLF or mixed line endings, a missing blank line between cues, text without a timing line, the `WEBVTT` header and VTT cue settings. Exit code 1 when it finds errors. | Fix anything by itself, or tell whether the timing matches the speech. |
| `shift` | Moves every cue earlier or later by the same amount (`--by 2.5`, `--by=-500ms`, `--by=+00:00:01,200`). Never writes a negative time (rule below). | Stretch or rescale timing (for example after a frame-rate change), or sync to audio. |
| `merge` | Joins two or more files into one, sorted by start time and renumbered. Inputs may mix SRT and VTT. | Resolve overlaps between the merged files. |
| `split` | Splits one file at time points (`--at 00:10:00`) or every N cues (`--every 200`). | Cut a cue in two, or move later parts back to 0:00 by itself (use `shift` afterwards). |

None of the tools create timing from audio or video, transcribe speech, or translate text. Encodings other than UTF-8 are not guessed: GBK, Shift_JIS or Windows-1252 files must be re-saved as UTF-8 first (UTF-16 with a BOM is read).

## Install

Requires Python 3.8 or later.

```bash
git clone https://github.com/yanaaibuilders/subtitle-skills.git
```

- **Claude Code:** copy `subtitle-skills/skills/subtitle-tools` to `~/.claude/skills/subtitle-tools` (all projects) or to `.claude/skills/subtitle-tools` inside one project.
- **Codex:** copy it to `~/.agents/skills/subtitle-tools` (all projects) or to `.agents/skills/subtitle-tools` inside one repository.
- **skills CLI** (Node.js 22.20 or later): `npx skills add yanaaibuilders/subtitle-skills`

Then ask in plain words, for example "check episode.srt for problems", "convert talk.vtt to SRT" or "move every subtitle in ep1.srt 1.2 seconds later".

## Use the script directly

```bash
cd subtitle-skills/skills/subtitle-tools
python3 scripts/subtool.py check samples/broken.srt
python3 scripts/subtool.py convert samples/demo.srt -o demo.vtt
python3 scripts/subtool.py convert samples/demo.vtt -o demo.txt
python3 scripts/subtool.py shift samples/demo.srt --by=-0.5s -o earlier.srt
python3 scripts/subtool.py merge samples/demo.srt samples/demo.vtt -o merged.srt
python3 scripts/subtool.py split samples/demo.srt --at 00:00:09 --out-dir parts
```

`samples/` holds three short files written for this repository: a clean SRT, a clean VTT with header, STYLE, identifiers, cue settings and inline timestamps, and a broken SRT with one example of each common problem.

Example `check` output:

```text
samples/broken.srt: SRT, 5 cue(s), UTF-8 with BOM, CRLF line endings
  warning file       UTF-8 BOM at the start; ... [bom]
  info    file       Windows line endings (CRLF); most players accept them, convert writes LF [crlf]
  warning line 6     starts at 00:00:02,500, before the cue at line 2 ends (00:00:03,000) [overlap]
  error   line 10    starts and ends at the same time (00:00:05,000) [zero-duration]
  ...
  result: 4 error(s), 3 warning(s)
```

Exit codes: `0` ok, `1` problems found or operation refused, `2` bad arguments.

## Rules worth knowing

- **Negative times.** `shift` refuses by default when any cue would start before 0:00. It names the cue, prints the smallest allowed offset, and writes nothing. With `--clamp`, start times below 0 are set to 0 and cues that would end at or before 0 are removed; the counts are printed.
- **Broken input.** `convert`, `shift`, `merge` and `split` still write output when the input has errors. Blocks they cannot read are skipped, and a note on stderr says how many errors were found. Run `check` to see them. Output is always UTF-8 without BOM, with LF line endings.
- **Split.** A cue belongs to the part in which it starts; a cue that crosses a split point stays whole in the earlier part. Times are not changed. If a part would be empty, nothing is written. Files are named `NAME.part1.srt`, `NAME.part2.srt`, ... and existing files with those names are overwritten.
- **Merge.** Cues with the same start time keep the order of the input files. VTT identifiers, NOTE, STYLE and REGION blocks are not carried over.
- **Tags.** SRT → VTT removes `<font>` and `{\an8}`-style tags and escapes `&`, `<`, `>`. VTT → SRT removes voice, class, ruby and timestamp tags and decodes `&amp;` `&lt;` `&gt;` and similar. VTT → VTT keeps the header, STYLE, REGION, identifiers and cue settings; NOTE blocks are dropped.

## Tests

```bash
python3 -m unittest discover -s skills/subtitle-tools/tests -v
```

## Need timing created from audio?

These tools only edit timing that already exists. If you have a script and its voiceover and need exact timing made from the audio, TimedSubs, a paid service from the same team, does that: [Script to SRT](https://timedsubs.com/en/features/script-to-srt?utm_source=github&utm_campaign=subtitle-skills).

## License

MIT. Copyright (c) 2026 Yana AI Builders. See [LICENSE](LICENSE).
