---
name: bilibili-video-summary
description: Summarize videos by extracting subtitles or transcribing audio. Use when a user provides a BV/av ID, Bilibili URL, or other video platform URL and wants a summary, transcript, or subtitle extraction.
---
# Video Summary

## How To Use

**Just run the script. No pre-checks needed.**

```bash
PYTHONUTF8=1 python video-summary.py "BV1fNw9ziEYk"
```

The script handles everything: subtitle extraction, STT fallback, model loading, caching.

If it exits with `[ACTION_REQUIRED]`, follow the instruction in the error message (e.g. ask the user for a project root path, then retry with `--project-root`). This only happens once — after that, the config is saved.

## DO NOT

- **DO NOT read `init.json` before running** — just run the script directly. It will tell you if something is missing.
- **DO NOT call B站 API directly** — returns `412`. The script uses `yutto` internally.
- **DO NOT change the STT model** unless the user explicitly asks. Default is `small`. Larger models are very slow on CPU.
- **DO NOT split/segment audio** — faster-whisper handles streaming internally.
- **DO NOT assume timeout = failure** — first STT run downloads the model. Use `--timeout 600000`.
- **DO NOT install dependencies without asking** the user.

## Options

```bash
# Custom project root (saved to init.json after first use)
PYTHONUTF8=1 python video-summary.py "BV1xxx" --project-root "D:/video-summary"

# Force STT (skip subtitle extraction)
PYTHONUTF8=1 python video-summary.py "BV1xxx" --force-stt

# Use a specific model (only if user asks)
PYTHONUTF8=1 python video-summary.py "BV1xxx" --stt-model medium

# Other platforms / local files
PYTHONUTF8=1 python video-summary.py "https://www.youtube.com/watch?v=xxx"
PYTHONUTF8=1 python video-summary.py "path/to/audio.mp3" --force-stt
```

## STT Model Reference

Default: **`small`** (244M, ~0.6 GB). Only change when the user explicitly asks.

| Model | Params | RAM | Speed | Notes |
|-------|--------|-----|-------|-------|
| `tiny` | 39M | ~0.3 GB | Fastest | Low quality |
| `base` | 74M | ~0.4 GB | Fast | |
| **`small`** | **244M** | **~0.6 GB** | **Balanced** | **Default** |
| `medium` | 769M | ~1.2 GB | Slow | |
| `turbo` | 809M | ~1.3 GB | Medium | large-v3-turbo |
| `large-v3` | 1.55B | ~1.8 GB | Very slow | 4-min video → 10+ min on CPU |

## Summarization

After getting the transcript, summarize in Chinese:

- 一句话总结视频核心内容
- 3-5 条关键要点（具体信息优先于笼统描述）
- 如能识别阶段/话题变化，补充时间线或章节导航
- 提炼具体数据、方法、结论和可执行建议
- 区分事实陈述与观点表达
