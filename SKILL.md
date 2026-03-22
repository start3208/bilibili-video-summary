---
name: bilibili-video-summary
description: Summarize videos by extracting subtitles or transcribing audio. Use when a user provides a BV/av ID, Bilibili URL, or other video platform URL and wants a summary, transcript, or subtitle extraction.
---
# Video Summary

## What This Script Does (So You Don't Have To)

The script `video-summary.py` handles the **entire** extraction pipeline automatically:

1. **Parse input** — accepts BV/av ID, bilibili URL, YouTube URL, or local file
2. **Try subtitles first** — uses `yutto` (bilibili) or `yt-dlp` (other platforms)
3. **Fall back to STT** — if no subtitles, downloads audio and transcribes with faster-whisper
4. **Auto-select STT model** — checks available RAM, ranks models best→worst, tries loading each in order, falls back on OOM (large-v3 → turbo → medium → small → base → tiny)
5. **Cache result** — saves transcript to `{projectRoot}/transcripts/bilibili-BVxxx-标题.txt`

**Your only jobs are:**
- Ensure `projectRoot` is set (ask user if empty)
- Run the script with `PYTHONUTF8=1`
- Wait for output (may take minutes on first STT run due to model download)
- Summarize the transcript text it prints to stdout

## DO NOT Do These Things

- **DO NOT call B站 API directly** (e.g. `api.bilibili.com`) — returns `412` anti-crawl. Always use the script which uses `yutto`.
- **DO NOT manually select or download STT models** — the script auto-detects RAM and handles fallback.
- **DO NOT split/segment audio yourself** — faster-whisper streams in 30s windows internally, no segmentation needed.
- **DO NOT assume the script failed if it runs for several minutes** — first-time STT requires model download (~1.5 GB). Use `--timeout 600000`.
- **DO NOT invent a `projectRoot` path** — always ask the user.
- **DO NOT install dependencies without asking** — the user may have a specific environment.

## Before Running

### 1. Check projectRoot (CRITICAL)

Read `init.json` first. If `projectRoot` is empty:
- **ASK the user** where they want transcripts stored (e.g. `D:/video-summary`)
- Pass the user's choice via `--project-root` on the first run; it will be saved for future use

### 2. Environment (Windows)

Always prefix with `PYTHONUTF8=1` to avoid GBK encoding errors:
```bash
PYTHONUTF8=1 python video-summary.py "BV1xxx" --project-root "D:/video-summary"
```

## Dependencies

- Required: `ffmpeg`, `ffprobe`
- For bilibili: `yutto` (`pip install yutto`)
- For other platforms: `yt-dlp`
- For STT fallback: `faster-whisper` (`pip install faster-whisper`)

## Usage

```bash
# First run — must specify project root (ask the user!)
PYTHONUTF8=1 python video-summary.py "BV1fNw9ziEYk" --project-root "D:/video-summary"

# Subsequent runs — projectRoot is saved in init.json
PYTHONUTF8=1 python video-summary.py "BV1fNw9ziEYk"

# Force speech-to-text (skip subtitles)
PYTHONUTF8=1 python video-summary.py "BV1fNw9ziEYk" --force-stt

# Pin a specific model (disables auto-fallback)
PYTHONUTF8=1 python video-summary.py "BV1fNw9ziEYk" --force-stt --stt-model medium

# Other platforms
PYTHONUTF8=1 python video-summary.py "https://www.youtube.com/watch?v=xxx"

# Local audio file
PYTHONUTF8=1 python video-summary.py "path/to/audio.mp3" --force-stt
```

Transcripts are cached. Repeated runs for the same video skip extraction and return instantly.

## STT Model Reference

Auto-selected by available RAM. The script handles this — you do not need to choose.

| Model | Parameters | RAM (CPU int8) | Notes |
|-------|-----------|----------------|-------|
| `large-v3` | 1.55B | ~1.8 GB | Best quality, 32 decoder layers |
| `turbo` | 809M | ~1.3 GB | large-v3-turbo, 4 decoder layers, ~8x faster |
| `medium` | 769M | ~1.2 GB | Balanced |
| `small` | 244M | ~0.6 GB | Lightweight |
| `base` | 74M | ~0.4 GB | Minimal |
| `tiny` | 39M | ~0.3 GB | Fastest, lowest quality |

## Summarization

After getting the transcript output, summarize it in Chinese:

- 一句话总结视频核心内容
- 3-5 条关键要点（具体信息优先于笼统描述）
- 如能识别阶段/话题变化，补充时间线或章节导航
- 提炼具体数据、方法、结论和可执行建议
- 区分事实陈述与观点表达
