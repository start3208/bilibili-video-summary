---
name: bilibili-video-summary
description: Summarize Bilibili videos, save transcripts, extract subtitles, or transcribe audio directly for BV/av IDs and Bilibili URLs. Use when a user wants a Bilibili summary, transcript, subtitle file, or says the subtitles are missing/wrong and wants audio STT instead. Check init.json or run init.py first for projectRoot and STT settings; interactive initialization must ask for projectRoot instead of assuming a default drive.
---
# Bilibili Video Summary

Use this skill when the input is a Bilibili `BV...` / `av...` id or a Bilibili URL and the user wants a summary or transcript.

## Quick Rules

1. Read `init.json` first. Treat `projectRoot` there as the source of truth unless the command explicitly passes `--project-root`.
2. If `init.json` is not fully initialized, the AI must stop and explicitly ask the user to confirm the initialization parameters before continuing.
3. The required parameters to confirm are `initialized`, `projectRoot`, `sttModel`, `hfEndpoint`, and `hfHome`. Pay special attention to `projectRoot` because it controls the output directory for downloads, transcripts, summaries, metadata, and cache artifacts.
4. If `projectRoot` is missing or unclear, ask the user where outputs should be stored. Do not invent a default path such as `F:\...`, and do not silently reuse the current workspace as the output directory unless the user explicitly approves it.
5. After the user confirms the parameters, run `init.py --init` or pass the confirmed values explicitly. If dependencies are missing, tell the user which ones need to be installed and pause for confirmation when appropriate.
6. When asking the user to confirm missing items, explain each parameter in plain language. Assume the user is not technical unless they clearly show otherwise.
7. If you tell the user something is missing, say what it affects in normal words. For example:
   - `projectRoot`: where the downloaded audio, transcripts, summaries, metadata, and cache files will be stored.
   - `sttModel`: the speech-to-text model size. Available names in this skill are `tiny`, `base`, `small`, `medium`, `large-v1`, `large-v2`, `large-v3`, `large-v3-turbo`, `large`, and `turbo`. In simple terms: smaller models are faster and use less memory, while larger models are usually more accurate but slower and heavier. For most users, `base` is the lighter option, `small` is a good balance, and `medium` is for users who want better accuracy and have a stronger machine.
   - `hfEndpoint`: the download mirror/source used to fetch speech model files.
   - `hfHome`: where the speech model cache is stored; blank means use the project cache folder.
   - dependencies such as `ffmpeg`, `ffprobe`, `yt-dlp`, `faster_whisper`: tools needed to download audio, inspect media files, and turn speech into text.
8. Prefer subtitles first.
9. If the user says subtitles are wrong, missing, or should be ignored, run `video-summary.py` with `--force-stt`.
10. Keep transcript artifacts. Do not overwrite old transcript files.
11. Do not switch to external ASR or upload audio unless the user explicitly approves it.

## Initialization

Run from the skill directory or use absolute paths.

```bash
# Inspect config and dependency status
python init.py --status

# Interactive initialization/update
python init.py --init

# Non-interactive initialization/update
python init.py --init --non-interactive --project-root "<PROJECT_ROOT>" --stt-model small --hf-endpoint https://hf-mirror.com --hf-home ""
```

Behavior:

- `init.py` stores reusable settings in `init.json`.
- `projectRoot` is required. If it is missing, `video-summary.py` should stop and tell the caller to initialize or pass `--project-root`.
- If initialization is incomplete, the AI should ask the user to confirm the initialization fields instead of guessing them.
- The most important confirmation item is `projectRoot`, because it determines the output location.
- When talking to the user about missing fields or dependencies, explain them in simple, non-jargon language first and only then mention the exact parameter name if needed.
- Interactive init prompts for missing values, especially `projectRoot`.
- `requirements.txt` installs Python packages only. `ffmpeg` / `ffprobe` still need to exist on the machine.

## Storage

Use `projectRoot` from `init.json` or `--project-root`.

- `downloads/`: raw yutto task folders and audio/subtitle artifacts
- `outputs/transcripts/`: retained raw transcript text
- `outputs/cleaned-transcripts/`: lightly cleaned transcript text
- `outputs/requests/`: structured summary requests
- `outputs/summaries/`: direct summary markdown files
- `outputs/metadata/`: per-run JSON records
- `outputs/index.jsonl`: append-only run index
- `cache/huggingface/`: faster-whisper model cache when `hfHome` is blank
- `temp/`: temporary files

Use unique timestamped file names. Keep the original title in metadata instead of the artifact file name.

## Main Workflow

1. Normalize the input (`BV...`, `av...`, or full Bilibili URL).
2. Check `init.json` / CLI overrides and ensure `projectRoot` is available.
3. If the user wants subtitles and has not asked to ignore them, try Bilibili subtitles first with `yutto`.
4. If subtitles are unavailable or the user explicitly wants audio STT, download audio and run `faster-whisper` on CPU.
5. Save transcript, cleaned transcript, request, summary, and metadata artifacts.
6. Emit stage logs and final status.

## Resume Semantics

- `--resume` alone may reuse the latest saved transcript when it is safe.
- `--resume --force-stt` must not blindly reuse an old transcript.
- With `--resume --force-stt`, prefer reusing saved audio and rerun STT from that audio.
- If no reusable audio exists, continue with a fresh audio download and STT run.

## Commands

```bash
# Standard run
python video-summary.py "BV1fNw9ziEYk"

# Transcript only via subtitle path
python video-summary.py "BV1fNw9ziEYk" --subtitle

# Ignore subtitles and transcribe from audio
python video-summary.py "BV1fNw9ziEYk" --force-stt --stt-model small

# Safer preset on constrained Windows machines
python video-summary.py "BV1fNw9ziEYk" --force-stt --low-memory --debug --keep-audio

# Reuse saved audio when possible, but still rerun STT
python video-summary.py "BV1fNw9ziEYk" --resume --force-stt --summary-mode standard --print-result

# Verify latest saved artifacts without rerunning extraction
python video-summary.py "BV1fNw9ziEYk" --verify

# Explicit project root override
python video-summary.py "BV1fNw9ziEYk" --project-root "<PROJECT_ROOT>"
```

## Notes

- `yutto` authentication is persistent on this machine once logged in.
- Current faster-whisper defaults are `language=zh`, `beam_size=5`, `vad_filter=True`, `segment_seconds=0`.
- `--low-memory` switches to a safer preset for constrained Windows machines.
- Delete temporary STT audio by default. Keep it only when debugging with `--keep-audio`.
