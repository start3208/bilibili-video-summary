#!/usr/bin/env python
"""Extract video transcript for AI summarization."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SKILL_DIR / "init.json"
AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".flac", ".aac", ".ogg", ".mp4", ".mkv", ".mov"}
DEBUG = False

# Whisper model specs for auto-selection (faster-whisper, CPU, int8 quantization).
# - params: from OpenAI official table
# - load_ram: model weights in int8 + CTranslate2 runtime overhead (~0.3 GB)
# - peak_ram: observed peak during long audio (~13 min) without segmentation
# With --segment-seconds, peak stays close to load_ram.
# Sources: https://github.com/openai/whisper
#          https://github.com/SYSTRAN/faster-whisper (benchmarks)
MODEL_SPECS = {
    #                params        load_ram  peak_ram (GB, CPU int8)
    "tiny":       {"params":  39_000_000, "load": 0.3, "peak": 0.4},
    "base":       {"params":  74_000_000, "load": 0.4, "peak": 0.5},
    "small":      {"params": 244_000_000, "load": 0.6, "peak": 0.8},
    "medium":     {"params": 769_000_000, "load": 1.2, "peak": 1.6},
    "turbo":      {"params": 809_000_000, "load": 1.3, "peak": 1.7},  # large-v3-turbo, 4 decoder layers
    "large-v3":   {"params":1_550_000_000,"load": 1.8, "peak": 2.3},  # 32 decoder layers
}

# Auto-segmentation threshold: if available RAM < peak but >= load, split audio
AUTO_SEGMENT_SEC = 120  # 2-minute chunks keep peak RAM close to load_ram


# ── Config ──────────────────────────────────────────────────────────────

def load_config() -> dict:
    defaults = {"projectRoot": "", "sttModel": ""}
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            defaults.update({k: v for k, v in data.items() if k in defaults})
        except Exception:
            pass
    return defaults


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_dirs(root: Path) -> dict[str, Path]:
    dirs = {
        "transcripts": root / "transcripts",
        "cache": root / "cache" / "huggingface",
        "temp": root / "temp",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


# ── Helpers ─────────────────────────────────────────────────────────────

def eprint(*a, **kw):
    print(*a, file=sys.stderr, **kw)


def debug(msg: str):
    if DEBUG:
        eprint(f"[debug] {msg}")


def run(cmd, *, check=True, env=None):
    debug(f"run: {subprocess.list2cmdline([str(x) for x in cmd])}")
    merged = os.environ.copy()
    merged.setdefault("PYTHONUTF8", "1")
    merged.setdefault("PYTHONIOENCODING", "utf-8")
    if env:
        merged.update(env)
    return subprocess.run(cmd, check=check, text=True, capture_output=True,
                          encoding="utf-8", errors="replace", env=merged)


def require_cmds(*names: str):
    missing = [n for n in names if not shutil.which(n)]
    if missing:
        raise SystemExit(f"缺少依赖: {', '.join(missing)}")


def get_total_ram_gb() -> float:
    """Return total physical RAM in GB (stable, unlike available RAM)."""
    try:
        import psutil
        return psutil.virtual_memory().total / (1024 ** 3)
    except ImportError:
        pass
    if sys.platform == "win32":
        try:
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            stat = MEMORYSTATUSEX(dwLength=ctypes.sizeof(MEMORYSTATUSEX))
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return stat.ullTotalPhys / (1024 ** 3)
        except Exception:
            pass
    else:
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) / (1024 ** 2)
        except Exception:
            pass
    return 8.0  # conservative fallback


def auto_select_model(total_ram_gb: float) -> tuple[str, int]:
    """Pick the best Whisper model based on total physical RAM.

    Uses total RAM (not available) for a stable, repeatable decision.
    Reserves ~4 GB for OS + typical apps (browser, IDE, etc.).

    Returns (model_name, segment_seconds).
    - If budget >= peak: no segmentation needed (segment_seconds=0)
    - If load <= budget < peak: use model with auto-segmentation to cap peak
    - Otherwise: try a smaller model
    """
    budget = total_ram_gb - 4.0  # reserve for OS + typical workload

    # Try from best to worst
    for name in ("large-v3", "turbo", "medium", "small", "base", "tiny"):
        spec = MODEL_SPECS[name]
        if budget >= spec["peak"]:
            return name, 0  # plenty of RAM, no segmentation
        if budget >= spec["load"] + 0.3:
            # Can load the model, but need segmentation to stay under peak
            return name, AUTO_SEGMENT_SEC
    return "tiny", AUTO_SEGMENT_SEC


def sanitize_filename(s: str, max_len: int = 80) -> str:
    """Clean a string for use in filenames."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    s = s.strip('. ')
    if len(s) > max_len:
        s = s[:max_len].rstrip()
    return s


def fetch_title_bilibili(url: str) -> str:
    """Get video title via yutto --info-only."""
    try:
        r = run([sys.executable, "-m", "yutto", url, "--info-only"], check=False)
        # yutto --info-only prints title in output
        for line in (r.stdout + r.stderr).splitlines():
            # yutto prints: Title: xxx  or just the title line
            if line.strip():
                # Look for a line that looks like a title
                m = re.match(r'(?:Title|标题)\s*[:：]\s*(.+)', line.strip(), re.I)
                if m:
                    return m.group(1).strip()
        # Fallback: try yt-dlp if available
        if shutil.which("yt-dlp"):
            return fetch_title_generic(url)
    except Exception:
        pass
    return ""


def fetch_title_generic(url: str) -> str:
    """Get video title via yt-dlp."""
    try:
        r = run(["yt-dlp", "--get-title", "--no-download", url], check=False)
        title = r.stdout.strip().splitlines()
        return title[0] if title else ""
    except Exception:
        return ""


# ── Input parsing ───────────────────────────────────────────────────────

def normalize_input(s: str) -> str:
    s = s.strip()
    if re.fullmatch(r"BV[0-9A-Za-z]+", s, re.I):
        return f"https://www.bilibili.com/video/{s}"
    if re.fullmatch(r"V[0-9A-Za-z]+", s, re.I):
        return f"https://www.bilibili.com/video/B{s}"
    if re.fullmatch(r"av\d+", s, re.I):
        return f"https://www.bilibili.com/video/{s}"
    return s


def detect_platform(s: str) -> str:
    lo = s.lower()
    if re.fullmatch(r"bv[0-9a-z]+|v[0-9a-z]+|av\d+", lo):
        return "bilibili"
    if "bilibili.com" in lo: return "bilibili"
    if "youtube.com" in lo or "youtu.be" in lo: return "youtube"
    if "xiaohongshu.com" in lo or "xhslink.com" in lo: return "xiaohongshu"
    if "douyin.com" in lo: return "douyin"
    if Path(s).exists(): return "local"
    return "unknown"


def extract_video_id(url: str, platform: str) -> str:
    if platform == "bilibili":
        m = re.search(r"/(BV[0-9A-Za-z]+)", url, re.I) or re.search(r"/(av\d+)", url, re.I)
        return m.group(1) if m else ""
    if platform == "youtube":
        m = re.search(r"[?&]v=([A-Za-z0-9_-]+)", url)
        return m.group(1) if m else ""
    return ""


def transcript_key(platform: str, video_id: str, title: str = "") -> str:
    base = f"{platform}-{video_id}" if video_id else f"{platform}-{int(time.time())}"
    if title:
        safe = sanitize_filename(title, max_len=60)
        if safe:
            return f"{base}-{safe}"
    return base


# ── Subtitle extraction ────────────────────────────────────────────────

def parse_subtitle_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".json":
        try:
            raw = json.loads(text)
            if isinstance(raw, dict) and isinstance(raw.get("events"), list):
                return "\n".join(
                    t for ev in raw["events"] for seg in ev.get("segs", [])
                    if (t := str(seg.get("utf8", "")).strip())
                )
        except Exception:
            pass
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s or re.fullmatch(r"\d+", s) or "-->" in s or s.upper().startswith("WEBVTT"):
            continue
        s = re.sub(r"<[^>]+>", " ", s)
        s = re.sub(r"\{\\.*?\}", " ", s)
        s = re.sub(r"&nbsp;| +", " ", s).strip()
        if s:
            lines.append(s)
    return "\n".join(lines)


def pick_best_subtitle(files: list[Path]) -> Path | None:
    if not files:
        return None
    for keywords in [("zh", "中文"), ("zh-cn",), ("zh-hans",), ("en", "english")]:
        for f in files:
            if any(k in f.name.lower() for k in keywords):
                return f
    return files[0]


def extract_subtitles_bilibili(url: str, work_dir: Path) -> str:
    require_cmds("python")
    sub_dir = work_dir / "subtitle"
    sub_dir.mkdir(parents=True, exist_ok=True)
    eprint("[info] 提取字幕 (yutto)...")
    try:
        run([sys.executable, "-m", "yutto", url, "--subtitle-only", "-d", str(sub_dir)])
    except Exception as e:
        debug(f"yutto subtitle failed: {e}")
        return ""
    best = pick_best_subtitle(sorted(sub_dir.glob("*.srt")))
    return parse_subtitle_file(best) if best else ""


def extract_subtitles_generic(url: str, lang: str, work_dir: Path) -> str:
    require_cmds("yt-dlp")
    for flag in ("--write-subs", "--write-auto-subs"):
        try:
            eprint(f"[info] 提取字幕 (yt-dlp {flag})...")
            run(["yt-dlp", "--skip-download", "--sub-langs", lang, flag,
                 "--convert-subs", "srt", "-P", str(work_dir), url])
        except Exception:
            continue
        files = list(work_dir.glob("*.srt")) + list(work_dir.glob("*.vtt")) + list(work_dir.glob("*.json"))
        best = pick_best_subtitle(files)
        if best:
            text = parse_subtitle_file(best)
            if text.strip():
                return text
    return ""


# ── STT ─────────────────────────────────────────────────────────────────

def ffprobe_duration(path: str) -> float:
    try:
        out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", path],
                  check=False).stdout.strip()
        return float(out or 0)
    except Exception:
        return 0.0


def load_whisper_model(cache_dir: Path, model_size: str):
    os.environ.setdefault("HF_HOME", str(cache_dir))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(cache_dir))
    hf = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")
    if hf:
        os.environ["HF_ENDPOINT"] = hf
    from faster_whisper import WhisperModel
    # Check if model is already cached
    model_dir = cache_dir / f"models--Systran--faster-whisper-{model_size}"
    if model_dir.exists():
        eprint(f"[info] 加载 STT 模型 ({model_size})...")
    else:
        eprint(f"[info] 首次使用，正在下载 STT 模型 ({model_size})，可能需要几分钟，请耐心等待...")
    try:
        return WhisperModel(model_size, device="cpu", compute_type="int8",
                            download_root=str(cache_dir))
    except Exception as e:
        raise SystemExit(f"STT 模型加载失败: {e}")


def transcribe(audio_path: Path, cache_dir: Path, model_size: str,
               language: str, vad: bool, beam: int, seg_sec: int) -> str:
    model = load_whisper_model(cache_dir, model_size)
    to_process = [audio_path]
    if seg_sec > 0 and ffprobe_duration(str(audio_path)) > seg_sec:
        seg_dir = audio_path.parent / "segments"
        seg_dir.mkdir(exist_ok=True)
        run(["ffmpeg", "-y", "-i", str(audio_path), "-f", "segment",
             "-segment_time", str(seg_sec), "-c:a", "pcm_s16le",
             "-ar", "16000", "-ac", "1", str(seg_dir / "seg-%03d.wav")])
        chunks = sorted(seg_dir.glob("seg-*.wav"))
        if chunks:
            to_process = chunks

    parts = []
    eprint("[info] 执行 STT 转录...")
    for sp in to_process:
        try:
            segs, _ = model.transcribe(str(sp), language=language,
                                       vad_filter=vad, beam_size=beam)
            for s in segs:
                t = s.text.strip()
                if t:
                    parts.append(t)
        except Exception as e:
            eprint(f"[warn] 转录失败: {sp.name} - {e}")
    return "\n".join(parts)


def download_audio_bilibili(url: str, work_dir: Path) -> Path | None:
    require_cmds("python")
    d = work_dir / "audio"
    d.mkdir(parents=True, exist_ok=True)
    eprint("[info] 下载音频 (yutto)...")
    try:
        run([sys.executable, "-m", "yutto", url, "--audio-only",
             "--no-subtitle", "--no-danmaku", "--no-cover", "-d", str(d)])
    except Exception as e:
        debug(f"yutto audio failed: {e}")
        return None
    files = [p for p in d.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS]
    return sorted(files)[0] if files else None


def download_audio_generic(url: str, work_dir: Path) -> Path | None:
    require_cmds("yt-dlp")
    d = work_dir / "audio"
    d.mkdir(parents=True, exist_ok=True)
    eprint("[info] 下载音频 (yt-dlp)...")
    try:
        run(["yt-dlp", "-x", "--audio-format", "mp3",
             "-o", str(d / "audio.%(ext)s"), url])
    except Exception as e:
        debug(f"yt-dlp audio failed: {e}")
        return None
    files = list(d.glob("audio.*"))
    return files[0] if files else None


# ── Main ────────────────────────────────────────────────────────────────

def main():
    # Force UTF-8 everywhere — prevents GBK errors on Windows
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    p = argparse.ArgumentParser(description="Extract video transcript for AI summarization")
    p.add_argument("input", help="BV/av ID, URL, or local file path")
    p.add_argument("--force-stt", action="store_true", help="Skip subtitles, transcribe from audio")
    p.add_argument("--project-root", help="Root dir for transcripts and cache")
    p.add_argument("--stt-model", help="Whisper model: tiny(39M)/base(74M)/small(244M)/medium(769M)/turbo(809M)/large-v3(1.55B)")
    p.add_argument("--low-memory", action="store_true", help="Conservative STT settings")
    p.add_argument("--beam-size", type=int, default=5)
    p.add_argument("--no-vad", action="store_true")
    p.add_argument("--segment-seconds", type=int, default=0)
    p.add_argument("--stt-language", default="zh")
    p.add_argument("--keep-audio", action="store_true")
    p.add_argument("--debug", action="store_true")
    p.add_argument("--lang", default="zh.*,zh-CN,zh-Hans,en.*", help="Subtitle language filter")
    args = p.parse_args()

    global DEBUG
    DEBUG = args.debug

    # Config
    cfg = load_config()
    project_root = args.project_root or cfg["projectRoot"]
    if not project_root:
        raise SystemExit(
            "projectRoot 未配置。请用 --project-root 指定，例如:\n"
            f'  python "{Path(__file__).name}" "BV1xxx" --project-root "D:/video-summary"'
        )
    project_root = Path(project_root)
    stt_model = args.stt_model or cfg["sttModel"] or ""

    # Auto-select model if not configured
    if not stt_model:
        total = get_total_ram_gb()
        stt_model, auto_seg = auto_select_model(total)
        seg_info = f"，自动分段 {auto_seg}s" if auto_seg else ""
        eprint(f"[info] 总内存 {total:.0f} GB → 自动选择 STT 模型: {stt_model}{seg_info}")
        # Apply auto-segmentation if user didn't specify
        if auto_seg and not args.segment_seconds:
            args.segment_seconds = auto_seg
        # Persist the auto-selected model
        cfg["sttModel"] = stt_model
        save_config(cfg)

    # Persist config on explicit --project-root
    if args.project_root:
        cfg["projectRoot"] = str(project_root)
        if args.stt_model:
            cfg["sttModel"] = args.stt_model
        save_config(cfg)

    # Low-memory preset
    if args.low_memory:
        stt_model = args.stt_model or "base"
        args.beam_size = 1
        if not args.segment_seconds:
            args.segment_seconds = 180

    dirs = ensure_dirs(project_root)
    require_cmds("ffmpeg", "ffprobe")

    # Parse input
    raw = args.input
    url = normalize_input(raw)
    platform = detect_platform(raw)
    vid = extract_video_id(url, platform)

    # Fetch video title for meaningful filenames
    title = ""
    if platform == "bilibili":
        title = fetch_title_bilibili(url)
    elif platform not in ("local", "unknown"):
        title = fetch_title_generic(url)
    if title:
        eprint(f"[info] 视频标题: {title}")

    key = transcript_key(platform, vid, title)
    tp = dirs["transcripts"] / f"{key}.txt"

    # Cache check — also try legacy key (without title) for backward compat
    legacy_tp = dirs["transcripts"] / f"{transcript_key(platform, vid)}.txt"
    if not tp.exists() and legacy_tp.exists() and not args.force_stt:
        # Rename legacy file to include title
        if title:
            legacy_tp.rename(tp)
            eprint(f"[info] 已将旧缓存重命名: {legacy_tp.name} -> {tp.name}")
        else:
            tp = legacy_tp

    # Cache check
    if tp.exists() and not args.force_stt:
        eprint(f"[info] 命中缓存: {tp}")
        print(tp.read_text(encoding="utf-8"))
        return

    # ── Extract transcript ──────────────────────────────────────────────
    transcript = ""

    # 1) Try subtitles (unless --force-stt)
    if not args.force_stt:
        with tempfile.TemporaryDirectory(prefix="vs-sub-") as td:
            if platform == "bilibili":
                transcript = extract_subtitles_bilibili(url, Path(td))
            elif platform != "local":
                transcript = extract_subtitles_generic(url, args.lang, Path(td))

    # 2) Fallback / forced STT
    if not transcript:
        if not args.force_stt:
            eprint("[info] 字幕不可用，转入 STT...")
        if platform == "local":
            transcript = transcribe(Path(raw), dirs["cache"], stt_model,
                                    args.stt_language, not args.no_vad,
                                    args.beam_size, args.segment_seconds)
        else:
            with tempfile.TemporaryDirectory(prefix="vs-audio-") as td:
                audio = (download_audio_bilibili(url, Path(td)) if platform == "bilibili"
                         else download_audio_generic(url, Path(td)))
                if not audio or not audio.exists():
                    raise SystemExit("无法获取音频文件")
                transcript = transcribe(audio, dirs["cache"], stt_model,
                                        args.stt_language, not args.no_vad,
                                        args.beam_size, args.segment_seconds)
                if args.keep_audio:
                    dst = dirs["temp"] / key
                    dst.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(audio), str(dst / audio.name))
                    eprint(f"[info] 音频已保留: {dst / audio.name}")

    if not transcript:
        raise SystemExit("无法提取字幕或完成转录。")

    # Clean and save
    transcript = re.sub(r"\s+", " ", transcript).strip()
    tp.parent.mkdir(parents=True, exist_ok=True)
    tp.write_text(transcript, encoding="utf-8")
    eprint(f"[info] transcript 已保存: {tp}")
    print(transcript)


if __name__ == "__main__":
    main()
