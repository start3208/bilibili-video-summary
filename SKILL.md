---
name: bilibili-video-summary
description: Summarize videos by extracting subtitles or transcribing audio. Use when a user provides a BV/av ID, Bilibili URL, or other video platform URL and wants a summary, transcript, or subtitle extraction.
---
# Video Summary

Extract video transcript (subtitles first, STT fallback), then summarize.

## Setup

First run requires `--project-root`:

```bash
python video-summary.py "BV1fNw9ziEYk" --project-root "D:/video-summary"
```

After first run, `projectRoot` is saved to `init.json` and reused automatically.

Dependencies: `ffmpeg`, `ffprobe`, Python packages in `requirements.txt`. For STT: `faster-whisper`. For bilibili: `yutto`. For other platforms: `yt-dlp`.

## Usage

```bash
# Standard (subtitles first, STT fallback)
python video-summary.py "BV1fNw9ziEYk"

# Force speech-to-text (skip subtitles)
python video-summary.py "BV1fNw9ziEYk" --force-stt

# Low-memory preset for constrained machines
python video-summary.py "BV1fNw9ziEYk" --force-stt --low-memory

# Other platforms
python video-summary.py "https://www.youtube.com/watch?v=xxx"

# Local audio file
python video-summary.py "path/to/audio.mp3" --force-stt
```

Transcripts are cached by video ID in `{projectRoot}/transcripts/`. Repeated runs skip extraction. Use `--force-stt` to re-transcribe.

## Summarization

After getting the transcript output, summarize it:

- 一句话总结
- 3-5 条关键要点
- 如能识别阶段/话题变化，补充时间线或章节导航
- 提炼具体信息、方法、结论和可执行建议
