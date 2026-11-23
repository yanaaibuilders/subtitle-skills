# subtitle-skills

免费的字幕文件工具，打包成 Claude Code 和 Codex 都能用的 Agent Skill。可以做 SRT 与 WebVTT 互转、导出纯文本、检查文件里的常见问题、整体平移时间、合并和拆分。整个工具是一个 Python 3 脚本，只用标准库，只处理本地文件，不发任何网络请求。

[English](README.md)

## 每个工具能做什么、不能做什么

| 工具 | 能做 | 不能做 |
|---|---|---|
| `convert` | SRT → VTT、VTT → SRT、SRT 或 VTT → TXT。SRT → SRT 会重排序号、去掉 BOM、换行统一成 LF。保留 `<i>` `<b>` `<u>`。 | 读写 ASS/SSA、SBV、TTML 等其他格式。输出 SRT 时保留 VTT 的位置设置（cue settings），因为 SRT 没有对应写法。 |
| `check` | 报告时间重叠、零时长或负时长、顺序颠倒、SRT 序号问题、非法 UTF-8、BOM、UTF-16、CRLF 或混用换行、两条字幕之间漏了空行、没有时间行的文字、`WEBVTT` 文件头和 VTT cue 设置。发现错误时退出码为 1。 | 自动修复问题，或判断时间轴和说话内容是否对得上。 |
| `shift` | 把所有字幕整体提前或推后同样的时间（`--by 2.5`、`--by=-500ms`、`--by=+00:00:01,200`）。不会写出负时间（规则见下文）。 | 按比例拉伸时间（比如改帧率之后），或按音频对齐。 |
| `merge` | 把两个或更多文件合成一个，按开始时间排序并重排序号。输入可以 SRT 和 VTT 混用。 | 处理合并后两份文件之间的重叠。 |
| `split` | 按时间点（`--at 00:10:00`）或每 N 条（`--every 200`）拆分一个文件。 | 把一条字幕切成两条，或自动把后面几份的时间挪回 0:00（之后用 `shift` 处理）。 |

所有工具都不能从音频或视频生成时间轴，不能转写语音，也不能翻译。不猜测 UTF-8 以外的编码：GBK、Shift_JIS、Windows-1252 的文件要先另存为 UTF-8（带 BOM 的 UTF-16 可以直接读）。

## 安装

需要 Python 3.8 或更高版本。

```bash
git clone https://github.com/yanaaibuilders/subtitle-skills.git
```

- **Claude Code：** 把 `subtitle-skills/skills/subtitle-tools` 复制到 `~/.claude/skills/subtitle-tools`（所有项目可用），或某个项目里的 `.claude/skills/subtitle-tools`。
- **Codex：** 复制到 `~/.agents/skills/subtitle-tools`（所有项目可用），或某个仓库里的 `.agents/skills/subtitle-tools`。
- **skills 命令行**（需要 Node.js 22.20 或更高）：`npx skills add yanaaibuilders/subtitle-skills`

装好后直接用自然语言提要求，比如"检查 episode.srt 有什么问题""把 talk.vtt 转成 SRT""把 ep1.srt 的字幕整体推后 1.2 秒"。

## 直接运行脚本

```bash
cd subtitle-skills/skills/subtitle-tools
python3 scripts/subtool.py check samples/broken.srt
python3 scripts/subtool.py convert samples/demo.srt -o demo.vtt
python3 scripts/subtool.py convert samples/demo.vtt -o demo.txt
python3 scripts/subtool.py shift samples/demo.srt --by=-0.5s -o earlier.srt
python3 scripts/subtool.py merge samples/demo.srt samples/demo.vtt -o merged.srt
python3 scripts/subtool.py split samples/demo.srt --at 00:00:09 --out-dir parts
```

`samples/` 里有三个为本仓库编写的短文件：一个干净的 SRT；一个干净的 VTT，带文件头、STYLE、标识符、cue 设置和行内时间戳；一个有问题的 SRT，每种常见问题各放了一个例子。

`check` 的输出示例：

```text
samples/broken.srt: SRT, 5 cue(s), UTF-8 with BOM, CRLF line endings
  warning file       UTF-8 BOM at the start; ... [bom]
  info    file       Windows line endings (CRLF); most players accept them, convert writes LF [crlf]
  warning line 6     starts at 00:00:02,500, before the cue at line 2 ends (00:00:03,000) [overlap]
  error   line 10    starts and ends at the same time (00:00:05,000) [zero-duration]
  ...
  result: 4 error(s), 3 warning(s)
```

退出码：`0` 正常；`1` 发现问题或拒绝执行；`2` 参数错误。

## 需要知道的规则

- **负时间。** 只要有一条字幕会被平移到 0:00 之前，`shift` 默认拒绝执行：它会指出是哪一条、给出允许的最小偏移量，并且不写任何文件。加 `--clamp` 时，小于 0 的开始时间改成 0，结束时间会落在 0 或更早的字幕直接删除，并打印各自的条数。
- **输入有错时。** `convert`、`shift`、`merge`、`split` 遇到有错误的输入仍会写出结果：读不懂的块会被跳过，stderr 上会提示发现了几个错误，用 `check` 可以看到明细。输出一律是不带 BOM 的 UTF-8，换行为 LF。
- **拆分。** 一条字幕归入它开始时所在的那一份；跨过拆分点的字幕整条留在前一份。时间不做改动。只要有一份会是空的，就什么都不写。文件名为 `名字.part1.srt`、`名字.part2.srt`……同名文件会被覆盖。
- **合并。** 开始时间相同的字幕按输入文件的先后排列。VTT 的标识符、NOTE、STYLE、REGION 块不会带进合并结果。
- **标签。** SRT → VTT 去掉 `<font>` 和 `{\an8}` 这类标签，并转义 `&`、`<`、`>`。VTT → SRT 去掉说话人、class、ruby 和时间戳标签，并还原 `&amp;` `&lt;` `&gt;` 等转义。VTT → VTT 保留文件头、STYLE、REGION、标识符和 cue 设置，NOTE 块会被去掉。

## 测试

```bash
python3 -m unittest discover -s skills/subtitle-tools/tests -v
```

## 需要从音频生成时间轴？

这些工具只能编辑已经存在的时间轴。如果你手里有文稿和对应的配音，需要按音频生成精确的时间轴，可以用同一团队做的付费服务 TimedSubs：[文稿转 SRT](https://timedsubs.com/zh/features/script-to-srt?utm_source=github&utm_campaign=subtitle-skills)。

## 许可证

MIT。Copyright (c) 2026 Yana AI Builders。见 [LICENSE](LICENSE)。

作者 Yana Li，更新动态见 [X](https://x.com/woshiyanali)。
