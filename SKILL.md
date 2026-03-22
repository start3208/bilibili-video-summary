---
name: bilibili-video-summary
description: Summarize videos by extracting subtitles or transcribing audio. Use when a user provides a BV/av ID, Bilibili URL, or other video platform URL and wants a summary, transcript, or subtitle extraction.
---

**直接运行，不需要预检查。** 脚本自动提取字幕（无字幕则转语音识别）、加载模型、缓存结果。

```bash
PYTHONUTF8=1 python video-summary.py "<BV号或URL>"
```

输出 `[ACTION_REQUIRED]` 时按提示操作（如询问用户存储路径后加 `--project-root` 重跑），仅首次发生。

使用 `--timeout 600000`，首次 STT 需下载模型，耗时较长属正常。

## 禁止事项

- 运行前读 `init.json` — 直接跑，缺什么脚本会说
- 直接调 B站 API — 会 412，脚本内部用 yutto
- 擅自换 STT 模型 — 默认 small，用户明确要求才加 `--stt-model`
- 自行切分音频 — faster-whisper 内部流式处理
- 未经询问安装依赖

## 参数

| 参数 | 说明 |
|------|------|
| `--project-root <路径>` | 转录文件存储目录（首次设置后自动保存） |
| `--force-stt` | 跳过字幕提取，强制语音转文字 |
| `--stt-model <名称>` | 指定模型（仅用户要求时使用） |

可用模型：`tiny`(39M) / `base`(74M) / **`small`**(244M,默认) / `medium`(769M) / `turbo`(809M) / `large-v3`(1.55B,极慢)

## 总结要求

拿到转录文本后用中文总结：

- 一句话概括核心内容
- 3-5 条关键要点，具体信息优先
- 如有阶段/话题变化，补充时间线
- 提炼数据、方法、结论和可执行建议
- 区分事实与观点
