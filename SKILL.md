---
name: bilibili-video-summary
description: Summarize videos by extracting subtitles or transcribing audio. Use when a user provides a BV/av ID, Bilibili URL, or other video platform URL and wants a summary, transcript, or subtitle extraction.
---
# Video Summary

Extract video transcript (subtitles first, STT fallback), then summarize.

## Before Running — MUST READ

### 1. Check projectRoot (CRITICAL)

Read `init.json` first. If `projectRoot` is empty:
- **ASK the user** where they want transcripts stored (e.g. `D:/video-summary`)
- **NEVER invent a path yourself** — always confirm with the user
- Pass the user's choice via `--project-root` on the first run; it will be saved for future use

### 2. Environment (Windows)

Set `PYTHONUTF8=1` before running to avoid GBK encoding errors:
```bash
PYTHONUTF8=1 python video-summary.py ...
```

### 3. STT Model (Auto-Selected)

If `sttModel` in `init.json` is empty, the script **auto-detects available RAM** and picks the best model that fits (faster-whisper, CPU, int8 quantization):

| Model | Parameters | RAM (CPU int8) | Notes |
|-------|-----------|----------------|-------|
| `large-v3` | 1.55B | ~5 GB | Best quality, 32 decoder layers |
| `turbo` | 809M | ~3 GB | large-v3-turbo, 4 decoder layers, ~8x faster than large |
| `medium` | 769M | ~2.5 GB | Balanced |
| `small` | 244M | ~1 GB | Lightweight |
| `base` | 74M | ~0.5 GB | Minimal |
| `tiny` | 39M | ~0.5 GB | Fastest, lowest quality |

The selected model is saved to `init.json` for future runs. Override with `--stt-model <name>`.

**First STT run downloads the model** (~500 MB+), which can take several minutes. This is normal — do NOT assume it failed or timed out. Use `--timeout 600000` (10 min) for the Bash call. Once downloaded, subsequent runs are fast.

## Dependencies

- Required: `ffmpeg`, `ffprobe`
- For bilibili: `yutto` (`pip install yutto`)
- For other platforms: `yt-dlp`
- For STT fallback: `faster-whisper` (`pip install faster-whisper`)

## Usage

```bash
# First run — must specify project root (ask the user!)
python video-summary.py "BV1fNw9ziEYk" --project-root "D:/video-summary"

# Subsequent runs — projectRoot is saved in init.json
python video-summary.py "BV1fNw9ziEYk"

# Force speech-to-text (skip subtitles)
python video-summary.py "BV1fNw9ziEYk" --force-stt

# Low-memory preset
python video-summary.py "BV1fNw9ziEYk" --force-stt --low-memory

# Other platforms
python video-summary.py "https://www.youtube.com/watch?v=xxx"

# Local audio file
python video-summary.py "path/to/audio.mp3" --force-stt
```

Transcripts are cached by video ID and title in `{projectRoot}/transcripts/`, e.g. `bilibili-BV1xxx-视频标题.txt`. Repeated runs skip extraction.

## Important Notes

- **Many B站 videos have NO subtitles** — STT fallback is common, not exceptional. Be patient with the first STT run.
- **Do NOT call B站 API directly** (e.g. `api.bilibili.com`) — it returns `412` due to anti-crawl. Always use `yutto` for bilibili content.
- **Encoding**: The script handles UTF-8 internally. If you see `UnicodeEncodeError` from other commands (e.g. `pip show`), prefix with `PYTHONUTF8=1`.
- **If the script exits with "projectRoot 未配置"**, you forgot to set it — ask the user and retry with `--project-root`.

## Summarization

After getting the transcript output, summarize it in Chinese:

- 一句话总结视频核心内容
- 3-5 条关键要点（具体信息优先于笼统描述）
- 如能识别阶段/话题变化，补充时间线或章节导航
- 提炼具体数据、方法、结论和可执行建议
- 区分事实陈述与观点表达
