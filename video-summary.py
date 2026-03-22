#!/usr/bin/env python
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
INIT_CONFIG_CANDIDATES = [SKILL_DIR / "init.json", SKILL_DIR.parent / "init.json"]
INIT_DEFAULTS = {
    "initialized": False,
    "projectRoot": "",
    "sttModel": "small",
    "hfEndpoint": "https://hf-mirror.com",
    "hfHome": "",
    "updatedAt": None,
}


def configure_stdio() -> None:
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def normalize_text(value, *, default=None, keep_blank=False):
    if value is None:
        return "" if keep_blank else default
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return "" if keep_blank else default
        return stripped
    return value


def normalize_init_config(data: dict | None) -> dict:
    merged = dict(INIT_DEFAULTS)
    if isinstance(data, dict):
        merged.update(data)
    merged["projectRoot"] = normalize_text(merged.get("projectRoot"), default="", keep_blank=True)
    merged["sttModel"] = normalize_text(merged.get("sttModel"), default=INIT_DEFAULTS["sttModel"])
    merged["hfEndpoint"] = normalize_text(merged.get("hfEndpoint"), default=INIT_DEFAULTS["hfEndpoint"])
    merged["hfHome"] = normalize_text(merged.get("hfHome"), default="", keep_blank=True)
    merged["initialized"] = bool(merged.get("initialized")) and bool(merged["projectRoot"])
    merged["updatedAt"] = merged.get("updatedAt") or None
    return merged


def load_init_config() -> dict:
    for path in INIT_CONFIG_CANDIDATES:
        if not path.exists():
            continue
        try:
            return normalize_init_config(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return normalize_init_config({})


def env_config_value(name: str, default=None, *, keep_blank: bool = False):
    raw = os.environ.get(name)
    if raw is None:
        return default if default is not None else ("" if keep_blank else default)
    return normalize_text(raw, default=default, keep_blank=keep_blank)


configure_stdio()
INIT_CONFIG = load_init_config()

OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
COOKIE_FILE_ENV = os.environ.get("VIDEO_SUMMARY_COOKIES", "")
COOKIE_BROWSER_ENV = os.environ.get("VIDEO_SUMMARY_COOKIES_FROM_BROWSER", "")
WHISPER_MODEL = os.environ.get("VIDEO_SUMMARY_WHISPER_MODEL", INIT_CONFIG.get("sttModel", "small"))
PROJECT_ROOT_ENV = env_config_value("VIDEO_SUMMARY_PROJECT_ROOT", INIT_CONFIG.get("projectRoot"), keep_blank=True)
HF_HOME_ENV = env_config_value("VIDEO_SUMMARY_HF_HOME", INIT_CONFIG.get("hfHome", ""), keep_blank=True)
HF_ENDPOINT_ENV = env_config_value("VIDEO_SUMMARY_HF_ENDPOINT", INIT_CONFIG.get("hfEndpoint", "https://hf-mirror.com"))
AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".flac", ".aac", ".ogg", ".mp4", ".mkv", ".mov"}
DEFAULT_STT_LANGUAGE = "zh"
DEFAULT_STT_BEAM_SIZE = 5
DEFAULT_SEGMENT_SECONDS = 0
LOW_MEMORY_STT_MODEL = "base"
LOW_MEMORY_BEAM_SIZE = 1
LOW_MEMORY_SEGMENT_SECONDS = 180
DEBUG = False


class StageTimer:
    def __init__(self, name: str):
        self.name = name
        self.started = 0.0

    def __enter__(self):
        self.started = time.perf_counter()
        eprint(f"[stage:start] {self.name}")
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed = time.perf_counter() - self.started
        status = "ok" if exc is None else "failed"
        eprint(f"[stage:end] {self.name} | status={status} | elapsed={elapsed:.2f}s")
        return False


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def log_kv(**kwargs):
    text = " | ".join(f"{key}={value}" for key, value in kwargs.items())
    eprint(f"[info] {text}")


def warn(message: str):
    eprint(f"[warn] {message}")


def debug_log(message: str):
    if DEBUG:
        eprint(f"[debug] {message}")


def has_cmd(name: str) -> bool:
    return shutil.which(name) is not None


def version_args(name: str) -> list[str]:
    return ["--version"] if name == "yt-dlp" else ["-version"]


def command_check(name: str, *args: str) -> tuple[bool, str]:
    resolved = shutil.which(name)
    if not resolved:
        return False, "not found"
    try:
        proc = subprocess.run(
            [name, *args],
            check=True,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        detail = (proc.stdout or proc.stderr).strip().splitlines()
        return True, (detail[0] if detail else resolved)
    except Exception as exc:
        return False, str(exc)


def require_cmds(*names: str):
    failures = []
    for name in names:
        ok, detail = command_check(name, *version_args(name)) if name != "python" else command_check(sys.executable, "--version")
        if not ok:
            failures.append(f"{name} ({detail})")
    if failures:
        raise SystemExit(f"Missing or unusable required dependencies: {', '.join(failures)}")


def run(cmd, check=True, capture=True, env=None, cwd=None):
    debug_log(f"run cwd={cwd or os.getcwd()} cmd={subprocess.list2cmdline([str(x) for x in cmd])}")
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    merged_env.setdefault("PYTHONUTF8", "1")
    merged_env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(
        cmd,
        check=check,
        text=True,
        capture_output=capture,
        encoding="utf-8",
        errors="replace",
        env=merged_env,
        cwd=cwd,
    )


def slugify(value: str) -> str:
    value = re.sub(r'[^A-Za-z0-9._-]+', "-", value).strip("-._")
    value = re.sub(r"-+", "-", value)
    return (value[:120] or f"task-{int(time.time())}")


def extract_video_id(value: str, platform: str) -> str:
    if platform == "bilibili":
        m = re.search(r"/(BV[0-9A-Za-z]+)", value, re.IGNORECASE)
        if m:
            return m.group(1)
        m = re.search(r"/(av\d+)", value, re.IGNORECASE)
        if m:
            return m.group(1)
    if platform == "youtube":
        m = re.search(r"[?&]v=([A-Za-z0-9_-]+)", value)
        if m:
            return m.group(1)
    return ""


def normalize_input(value: str) -> str:
    s = value.strip()
    if re.fullmatch(r"BV[0-9A-Za-z]+", s, re.IGNORECASE):
        return f"https://www.bilibili.com/video/{s}"
    if re.fullmatch(r"V[0-9A-Za-z]+", s, re.IGNORECASE):
        warn(f"检测到疑似缺少前缀 B 的 BV 号，已自动修正：{s} -> B{s}")
        return f"https://www.bilibili.com/video/B{s}"
    if re.fullmatch(r"av\d+", s, re.IGNORECASE):
        return f"https://www.bilibili.com/video/{s}"
    return s


def detect_platform(value: str) -> str:
    lower = value.lower()
    if Path(value).exists():
        return "local"
    if re.fullmatch(r"bv[0-9a-z]+", lower) or re.fullmatch(r"v[0-9a-z]+", lower) or re.fullmatch(r"av\d+", lower):
        return "bilibili"
    if "youtube.com" in lower or "youtu.be" in lower:
        return "youtube"
    if "bilibili.com" in lower:
        return "bilibili"
    if "xiaohongshu.com" in lower or "xhslink.com" in lower:
        return "xiaohongshu"
    if "douyin.com" in lower or "v.douyin.com" in lower:
        return "douyin"
    return "unknown"


def ensure_project_dirs(project_root: Path) -> dict[str, Path]:
    dirs = {
        "root": project_root,
        "models": project_root / "models",
        "temp": project_root / "temp",
        "downloads": project_root / "downloads",
        "outputs": project_root / "outputs",
        "transcripts": project_root / "outputs" / "transcripts",
        "cleaned_transcripts": project_root / "outputs" / "cleaned-transcripts",
        "requests": project_root / "outputs" / "requests",
        "summaries": project_root / "outputs" / "summaries",
        "metadata": project_root / "outputs" / "metadata",
        "cache": project_root / "cache",
        "hf": Path(HF_HOME_ENV) if HF_HOME_ENV else project_root / "cache" / "huggingface",
        "logs": project_root / "logs",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def init_config_path() -> Path:
    for path in INIT_CONFIG_CANDIDATES:
        if path.exists():
            return path
    return INIT_CONFIG_CANDIDATES[0]


def resolve_project_root(value: str) -> str:
    project_root = normalize_text(value, default="", keep_blank=True)
    if project_root:
        return project_root
    raise SystemExit(
        "projectRoot 未配置。请先运行 "
        f"\"{sys.executable}\" \"{SKILL_DIR / 'init.py'}\" --init "
        "完成交互式初始化，或在命令行显式传入 --project-root。"
        f" 当前配置文件: {init_config_path()}"
    )


def build_cookie_args(cookie_file: str = "", cookies_from_browser: str = ""):
    cookie = cookie_file or COOKIE_FILE_ENV
    if cookie:
        candidate = Path(cookie).expanduser()
        if candidate.exists():
            return ["--cookies", str(candidate)]
    browser = (cookies_from_browser or COOKIE_BROWSER_ENV).strip()
    if browser:
        return ["--cookies-from-browser", browser]
    return []


def ffprobe_duration(path: str) -> float:
    try:
        out = run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
        ]).stdout.strip()
        return float(out or 0)
    except Exception:
        return 0.0


def get_video_info(url: str, cookie_file: str, cookies_from_browser: str):
    cmd = ["yt-dlp", *build_cookie_args(cookie_file, cookies_from_browser), "--dump-json", "--no-download", url]
    out = run(cmd).stdout.strip()
    data = json.loads(out.splitlines()[-1])
    return {
        "title": data.get("title") or "Unknown",
        "duration": float(data.get("duration") or 0),
        "author": data.get("uploader") or data.get("channel") or "Unknown",
        "platform": data.get("extractor_key") or detect_platform(url),
    }


def parse_subtitle_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".json":
        try:
            raw = json.loads(text)
            if isinstance(raw, dict) and isinstance(raw.get("events"), list):
                parts = []
                for ev in raw["events"]:
                    for seg in ev.get("segs", []):
                        t = str(seg.get("utf8", "")).strip()
                        if t:
                            parts.append(t)
                return "\n".join(parts)
        except Exception:
            pass
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s or re.fullmatch(r"\d+", s) or "-->" in s or s.upper().startswith("WEBVTT"):
            continue
        s = re.sub(r"<[^>]+>", " ", s)
        s = re.sub(r"\{\\.*?\}", " ", s)
        s = re.sub(r"&nbsp;", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        if s:
            lines.append(s)
    return "\n".join(lines)


def pick_best_subtitle(files: list[Path]) -> Path | None:
    if not files:
        return None
    priorities = [
        ("zh", "中文"),
        ("zh-cn", "中文"),
        ("zh-hans", "中文"),
        ("english", "en"),
    ]
    lowered = [(f, f.name.lower()) for f in files]
    for keys in priorities:
        for f, name in lowered:
            if any(k in name for k in keys):
                return f
    return files[0]


def clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text


def build_cleaned_transcript(text: str) -> str:
    cleaned = text.replace(" ,", ",").replace(" .", ".")
    cleaned = re.sub(r"([。！？!?])\s*", r"\1\n", cleaned)
    cleaned = re.sub(r"([，；：])\s*", r"\1", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.replace(" \n", "\n").replace("\n ", "\n")
    return cleaned.strip()


def save_text(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def save_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return path


def artifact_prefix(platform: str, video_id: str) -> str:
    return slugify("-".join([platform, video_id or "noid"]))


def find_latest_run_metadata(dirs: dict[str, Path], prefix: str) -> Path | None:
    files = sorted(dirs["metadata"].glob(f"{prefix}-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def find_latest_downloaded_audio(dirs: dict[str, Path], prefix: str) -> Path | None:
    run_dirs = sorted(dirs["downloads"].glob(f"{prefix}-*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for run_dir in run_dirs:
        for candidate in [run_dir / "audio", run_dir]:
            if not candidate.exists():
                continue
            files = sorted([p for p in candidate.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO_EXTS], key=lambda p: p.stat().st_mtime, reverse=True)
            if files:
                return files[0]
    return None


def yutto_env(dirs: dict[str, Path]) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("HF_HOME", str(dirs["hf"]))
    env.setdefault("HUGGINGFACE_HUB_CACHE", str(dirs["hf"]))
    hf_endpoint = normalize_text(HF_ENDPOINT_ENV, default=INIT_DEFAULTS["hfEndpoint"])
    if hf_endpoint:
        env.setdefault("HF_ENDPOINT", hf_endpoint)
    return env


def extract_bilibili_subtitles_with_yutto(url: str, dirs: dict[str, Path], slug: str) -> tuple[str, dict]:
    require_cmds("python")
    task_dir = dirs["downloads"] / slug / "subtitle"
    task_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "yutto", url, "--subtitle-only", "-d", str(task_dir)]
    with StageTimer("提取字幕(yutto)"):
        proc = run(cmd, env=yutto_env(dirs))
    files = sorted(task_dir.glob("*.srt"))
    best = pick_best_subtitle(files)
    if not best:
        return "", {
            "mode": "subtitle-auto",
            "task_dir": str(task_dir),
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    text = parse_subtitle_file(best)
    debug_log(f"subtitle selected={best}")
    return text, {
        "mode": "subtitle-auto",
        "task_dir": str(task_dir),
        "subtitle_files": [str(p) for p in files],
        "selected_subtitle": str(best),
    }


def build_whisper_model(dirs: dict[str, Path], model_size: str):
    env = yutto_env(dirs)
    os.environ.update({
        "HF_HOME": env["HF_HOME"],
        "HUGGINGFACE_HUB_CACHE": env["HUGGINGFACE_HUB_CACHE"],
        "PYTHONUTF8": env["PYTHONUTF8"],
        "PYTHONIOENCODING": env["PYTHONIOENCODING"],
    })
    if env.get("HF_ENDPOINT"):
        os.environ["HF_ENDPOINT"] = env["HF_ENDPOINT"]
    elif "HF_ENDPOINT" in os.environ:
        os.environ.pop("HF_ENDPOINT", None)
    from faster_whisper import WhisperModel

    with StageTimer("加载 STT 模型"):
        try:
            debug_log(f"whisper init model={model_size} device=cpu compute_type=int8 download_root={dirs['hf']}")
            return WhisperModel(model_size, device="cpu", compute_type="int8", download_root=str(dirs["hf"]))
        except Exception as e:
            message = str(e)
            if "mkl_malloc" in message.lower() or "failed to allocate memory" in message.lower():
                raise SystemExit(f"STT 模型不可用[memory-allocation]：{e}。请释放内存、增大页文件、或改用更小模型后重试。")
            raise SystemExit(f"STT 模型不可用[model-load]：{e}。请检查当前项目缓存目录或 HuggingFace 镜像连通性，且不要擅自改用外部 ASR。")


def split_audio_for_stt(audio_path: Path, segment_seconds: int, task_dir: Path | None) -> list[Path]:
    if segment_seconds <= 0:
        return [audio_path]
    segment_root = (task_dir or audio_path.parent) / "segments"
    segment_root.mkdir(parents=True, exist_ok=True)
    outtpl = str(segment_root / "segment-%03d.wav")
    with StageTimer("切分长音频"):
        run([
            "ffmpeg", "-y", "-i", str(audio_path),
            "-f", "segment",
            "-segment_time", str(segment_seconds),
            "-c:a", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            outtpl,
        ])
    segments = sorted(segment_root.glob("segment-*.wav"))
    if not segments:
        raise SystemExit(f"音频切分失败：{audio_path}")
    debug_log(f"audio segmented count={len(segments)} dir={segment_root}")
    return segments


def transcribe_audio_path(audio_path: Path, dirs: dict[str, Path], model_size: str, keep_audio: bool, source_mode: str, task_dir: Path | None = None, language: str = DEFAULT_STT_LANGUAGE, vad_filter: bool = True, beam_size: int = DEFAULT_STT_BEAM_SIZE, segment_seconds: int = DEFAULT_SEGMENT_SECONDS) -> tuple[str, dict]:
    audio_duration = round(ffprobe_duration(str(audio_path)), 2)
    log_kv(audio_source=str(audio_path), audio_duration_sec=audio_duration, keep_audio=keep_audio)
    model = build_whisper_model(dirs, model_size)
    stt_params = {
        "model": model_size,
        "device": "cpu",
        "compute_type": "int8",
        "language": language,
        "vad_filter": vad_filter,
        "beam_size": beam_size,
        "segment_seconds": segment_seconds,
    }
    debug_log(f"stt params={json.dumps(stt_params, ensure_ascii=False)}")

    segment_paths = split_audio_for_stt(audio_path, segment_seconds, task_dir) if segment_seconds and audio_duration > segment_seconds else [audio_path]
    segment_details = []
    parts = []
    seg_count = 0
    covered_duration = 0.0
    last_info = None

    with StageTimer("执行 STT 转录"):
        for index, segment_path in enumerate(segment_paths, start=1):
            segment_duration = round(ffprobe_duration(str(segment_path)), 2)
            debug_log(f"transcribe segment index={index} path={segment_path} duration_sec={segment_duration}")
            try:
                segments, info = model.transcribe(str(segment_path), language=language, vad_filter=vad_filter, beam_size=beam_size)
                local_count = 0
                local_parts = []
                for seg in segments:
                    local_count += 1
                    seg_count += 1
                    seg_text = seg.text.strip()
                    if seg_text:
                        local_parts.append(seg_text)
                parts.extend(local_parts)
                last_info = info
                covered_duration += segment_duration
                segment_details.append({
                    "index": index,
                    "path": str(segment_path),
                    "duration_sec": segment_duration,
                    "status": "success",
                    "transcript_segments": local_count,
                })
            except Exception as exc:
                warn(f"音频分段转录失败: index={index} path={segment_path} error={exc}")
                segment_details.append({
                    "index": index,
                    "path": str(segment_path),
                    "duration_sec": segment_duration,
                    "status": "failed",
                    "error": str(exc),
                })

    failed_segments = [item for item in segment_details if item.get("status") != "success"]

    if keep_audio:
        audio_deleted = False
    else:
        audio_deleted = True
        cleanup_candidates = [audio_path] + [p for p in segment_paths if p != audio_path]
        for candidate in cleanup_candidates:
            try:
                if candidate.exists():
                    candidate.unlink()
            except Exception:
                audio_deleted = False
                warn(f"未能删除临时音频：{candidate}")

    language_value = getattr(last_info, "language", language) if last_info else language
    language_probability = round(getattr(last_info, "language_probability", 0) or 0, 4) if last_info else 0
    duration_value = round(covered_duration or getattr(last_info, "duration", 0) or audio_duration, 2) if last_info else round(covered_duration or audio_duration, 2)

    return "\n".join(parts), {
        "mode": source_mode,
        "task_dir": str(task_dir) if task_dir else "",
        "audio_deleted": audio_deleted,
        "audio_source": str(audio_path),
        "audio_duration_sec": audio_duration,
        "model": model_size,
        "stt_params": stt_params,
        "duration_sec": duration_value,
        "covered_duration_sec": duration_value,
        "language": language_value,
        "language_probability": language_probability,
        "segment_count": seg_count,
        "audio_segment_count": len(segment_paths),
        "segment_details": segment_details,
        "failed_segments": failed_segments,
    }


def transcribe_bilibili_with_faster_whisper(url: str, dirs: dict[str, Path], slug: str, model_size: str, keep_audio: bool, language: str, vad_filter: bool, beam_size: int, segment_seconds: int) -> tuple[str, dict]:
    require_cmds("python")
    task_dir = dirs["downloads"] / slug / "audio"
    task_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "yutto",
        url,
        "--audio-only",
        "--no-subtitle",
        "--no-danmaku",
        "--no-cover",
        "-d",
        str(task_dir),
    ]
    with StageTimer("下载音频(yutto)"):
        run(cmd, env=yutto_env(dirs))
    audio_files = sorted([p for p in task_dir.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS])
    if not audio_files:
        return "", {"mode": "stt-fallback", "task_dir": str(task_dir), "error": "audio-not-found"}
    return transcribe_audio_path(audio_files[0], dirs, model_size, keep_audio, "stt-fallback", task_dir, language=language, vad_filter=vad_filter, beam_size=beam_size, segment_seconds=segment_seconds)


def extract_subtitles_generic(url: str, lang: str, cookie_file: str, cookies_from_browser: str) -> str:
    with tempfile.TemporaryDirectory(prefix="video-summary-subs-") as tmpdir:
        common = ["yt-dlp", *build_cookie_args(cookie_file, cookies_from_browser), "--skip-download", "--sub-langs", lang, "-P", tmpdir, url]
        commands = [
            common[:1] + common[1:-1] + ["--write-subs", "--convert-subs", "srt", url],
            common[:1] + common[1:-1] + ["--write-auto-subs", "--convert-subs", "srt", url],
        ]
        for cmd in commands:
            try:
                with StageTimer("提取字幕(yt-dlp)"):
                    run(cmd)
            except Exception:
                continue
            files = list(Path(tmpdir).glob("*.srt")) + list(Path(tmpdir).glob("*.vtt")) + list(Path(tmpdir).glob("*.json"))
            best = pick_best_subtitle(files)
            if best:
                text = parse_subtitle_file(best)
                if text.strip():
                    return text
    return ""


def transcribe_local_file_with_faster_whisper(input_path: str, dirs: dict[str, Path], model_size: str, keep_audio: bool, language: str, vad_filter: bool, beam_size: int, segment_seconds: int) -> tuple[str, dict]:
    return transcribe_audio_path(Path(input_path), dirs, model_size, keep_audio=True if Path(input_path).exists() else keep_audio, source_mode="stt-fallback", language=language, vad_filter=vad_filter, beam_size=beam_size, segment_seconds=segment_seconds)


def transcribe_remote_with_yt_dlp(url: str, cookie_file: str, cookies_from_browser: str, dirs: dict[str, Path], model_size: str, keep_audio: bool, slug: str, source_mode: str, language: str, vad_filter: bool, beam_size: int, segment_seconds: int) -> tuple[str, dict]:
    task_dir = dirs["temp"] / slug / "remote-audio"
    task_dir.mkdir(parents=True, exist_ok=True)
    outtpl = str(task_dir / "audio.%(ext)s")
    with StageTimer("下载音频(yt-dlp)"):
        run(["yt-dlp", *build_cookie_args(cookie_file, cookies_from_browser), "-x", "--audio-format", "mp3", "-o", outtpl, url])
    files = list(task_dir.glob("audio.*"))
    if not files:
        return "", {"mode": source_mode, "task_dir": str(task_dir), "error": "audio-not-found"}
    return transcribe_audio_path(files[0], dirs, model_size, keep_audio, source_mode, task_dir, language=language, vad_filter=vad_filter, beam_size=beam_size, segment_seconds=segment_seconds)


def transcript_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?])\s+|\n+", text)
    cleaned = []
    for part in parts:
        item = part.strip()
        if item:
            cleaned.append(item)
    return cleaned


def build_summary_text(transcript: str, title: str, author: str, duration: float, source_meta: dict, mode: str) -> str:
    sentences = transcript_sentences(transcript)
    first_sentence = sentences[0] if sentences else transcript[:120]
    key_points = []
    seen = set()
    for sentence in sentences:
        normalized = sentence.strip()
        if len(normalized) < 18:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        key_points.append(normalized)
        if len(key_points) >= 5:
            break

    if mode == "short":
        key_points = key_points[:3]
    elif mode == "technical":
        key_points = key_points[:5]
    else:
        key_points = key_points[:4]

    quality_notes = source_meta.get("quality_issues") or []
    lines = [
        f"# {title}",
        f"- 作者: {author}",
        f"- 时长(秒): {int(duration or 0)}",
        f"- Source Mode: {source_meta.get('mode', 'unknown')}",
        f"- Summary Mode: {mode}",
        "",
        "## 一句话总结",
        first_sentence,
        "",
        "## 关键要点",
    ]
    lines.extend([f"- {item}" for item in key_points] or ["- （无可用要点）"])
    if mode == "technical":
        lines.extend([
            "",
            "## 技术观察",
            f"- STT 参数: {json.dumps(source_meta.get('stt_params') or {}, ensure_ascii=False)}",
            f"- 最终状态: {source_meta.get('final_status', 'unknown')}",
            f"- 覆盖时长(秒): {source_meta.get('covered_duration_sec', source_meta.get('duration_sec', 0))}",
        ])
    if quality_notes:
        lines.extend(["", "## Warnings", *[f"- {item}" for item in quality_notes]])
    return "\n".join(lines)


def apply_stt_preset(args) -> None:
    if not getattr(args, "low_memory", False):
        return
    if args.stt_model == WHISPER_MODEL:
        args.stt_model = LOW_MEMORY_STT_MODEL
    if args.beam_size == DEFAULT_STT_BEAM_SIZE:
        args.beam_size = LOW_MEMORY_BEAM_SIZE
    if args.segment_seconds == DEFAULT_SEGMENT_SECONDS:
        args.segment_seconds = LOW_MEMORY_SEGMENT_SECONDS
    source_vad = getattr(args, "vad_filter", True)
    args.stt_preset = "low-memory"
    debug_log(f"applied low-memory preset model={args.stt_model} beam_size={args.beam_size} segment_seconds={args.segment_seconds} vad_filter={source_vad}")


def build_stt_audit_notes(args) -> list[str]:
    notes = []
    notes.append(f"language={args.stt_language}: 对中文 B 站视频默认合理。")
    if args.beam_size <= 1:
        notes.append("beam_size<=1: 更省内存更稳，但可能略降识别质量。")
    elif args.beam_size <= 5:
        notes.append("beam_size=2-5: 质量/资源折中，适合作为默认值。")
    else:
        notes.append("beam_size>5: 资源占用更高，长音频或低内存机器上不推荐。")
    if args.vad_filter:
        notes.append("vad_filter=True: 对长音频和停顿较多内容更稳，但可能切掉极短弱音。")
    else:
        notes.append("vad_filter=False: 保守保留语音，适合排查 VAD 误裁剪问题。")
    if args.segment_seconds and args.segment_seconds > 0:
        notes.append(f"segment_seconds={args.segment_seconds}: 已启用分段转录，适合长音频和低内存场景。")
    else:
        notes.append("segment_seconds=0: 整段转录，适合较短音频或内存充足场景。")
    if getattr(args, "low_memory", False):
        notes.append("low-memory preset: 自动下调模型/beam 并启用分段，更适合本机当前内存条件。")
    return notes


def build_request(transcript: str, title: str, platform: str, author: str, duration: float, chapter: bool, as_json: bool, transcript_path: Path, source_meta: dict):
    request = {
        "title": title,
        "platform": platform,
        "author": author,
        "durationSeconds": int(duration or 0),
        "summaryMode": "chapter" if chapter else "standard",
        "llm": {
            "endpoint": f"{OPENAI_BASE_URL.rstrip('/')}/v1/chat/completions",
            "model": OPENAI_MODEL,
        },
        "transcriptPath": str(transcript_path),
        "source": source_meta,
        "instructions": [
            "阅读 transcript 字段中的视频文字内容。",
            "先给一句话总结，再给 3-5 条关键要点。",
            "如果内容里能识别出阶段变化，补充时间线或章节导航。",
            "尽量提炼具体信息、方法、结论和可执行建议。",
        ],
        "transcript": transcript,
    }
    if as_json:
        return json.dumps(request, ensure_ascii=False, indent=2)
    lines = [
        "# Video Summary Request",
        f"- 标题: {title}",
        f"- 平台: {platform}",
        f"- 作者: {author}",
        f"- 时长(秒): {int(duration or 0)}",
        f"- 模式: {'chapter' if chapter else 'standard'}",
        f"- Transcript Path: {transcript_path}",
        f"- Source Mode: {source_meta.get('mode', 'unknown')}",
        f"- Final Status: {source_meta.get('final_status', 'unknown')}",
        f"- LLM Endpoint: {request['llm']['endpoint']}",
        f"- Model: {request['llm']['model']}",
        "",
        "## 要求",
        "- 一句话总结",
        "- 3-5 条关键要点",
        "- 如可行，补充时间线/章节导航",
        "- 提炼具体信息、方法、结论和可执行建议",
        "",
    ]
    issues = source_meta.get("quality_issues") or []
    if issues:
        lines.extend(["## Warnings", *[f"- {item}" for item in issues], ""])
    lines.extend(["## Transcript", transcript])
    return "\n".join(lines)


def build_run_record(task_slug: str, input_value: str, info: dict, source_meta: dict, transcript_path: Path, cleaned_transcript_path: Path | None, summary_path: Path | None, request_path: Path | None, metadata_path: Path | None, total_elapsed: float, args) -> dict:
    return {
        "taskSlug": task_slug,
        "input": input_value,
        "title": info.get("title", "Unknown"),
        "platform": info.get("platform", "unknown"),
        "author": info.get("author", "Unknown"),
        "durationSeconds": round(float(info.get("duration") or 0), 2),
        "source": source_meta,
        "transcriptPath": str(transcript_path),
        "cleanedTranscriptPath": str(cleaned_transcript_path) if cleaned_transcript_path else "",
        "summaryPath": str(summary_path) if summary_path else "",
        "requestPath": str(request_path) if request_path else "",
        "metadataPath": str(metadata_path) if metadata_path else "",
        "elapsedSeconds": round(total_elapsed, 2),
        "resumeRequested": getattr(args, "resume", False),
        "verifyRequested": getattr(args, "verify", False),
        "sttModel": getattr(args, "stt_model", ""),
        "summaryMode": getattr(args, "summary_mode", "standard"),
        "sttPreset": getattr(args, "stt_preset", "default"),
    }


def verify_run_artifacts(run_record: dict) -> tuple[str, list[str]]:
    issues = []
    transcript_path = Path(run_record.get("transcriptPath") or "")
    cleaned_transcript_path = Path(run_record.get("cleanedTranscriptPath") or "") if run_record.get("cleanedTranscriptPath") else None
    summary_path = Path(run_record.get("summaryPath") or "") if run_record.get("summaryPath") else None
    request_path = Path(run_record.get("requestPath") or "") if run_record.get("requestPath") else None
    if not transcript_path.exists():
        issues.append(f"transcript 不存在：{transcript_path}")
    if cleaned_transcript_path and not cleaned_transcript_path.exists():
        issues.append(f"cleaned transcript 不存在：{cleaned_transcript_path}")
    if summary_path and not summary_path.exists():
        issues.append(f"summary 不存在：{summary_path}")
    if request_path and not request_path.exists():
        issues.append(f"request 不存在：{request_path}")
    source_meta = run_record.get("source") or {}
    if source_meta.get("final_status") in {"failed", "partial", "degraded"}:
        issues.append(f"source final_status={source_meta.get('final_status')}")
    return ("partial" if issues else "success"), issues


def cleanup_paths(dirs: dict[str, Path], clear_cache: bool, clear_temp: bool):
    if clear_temp and dirs["temp"].exists():
        for child in dirs["temp"].iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
    if clear_cache and dirs["cache"].exists():
        for child in dirs["cache"].iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)


def assess_transcript_quality(transcript: str, video_duration_sec: float, source_meta: dict) -> tuple[str, list[str]]:
    issues = []
    degraded_reasons = []
    status = "success"
    transcript_chars = len(transcript)
    audio_duration = float(source_meta.get("audio_duration_sec") or 0)
    stt_duration = float(source_meta.get("duration_sec") or 0)
    segment_count = int(source_meta.get("segment_count") or 0)
    mode = source_meta.get("mode", "unknown")

    if not transcript.strip():
        return "failed", ["transcript 为空"]

    if "�" in transcript:
        degraded_reasons.append("transcript 中包含乱码替代字符，质量已降级")

    failed_segments = source_meta.get("failed_segments") or []

    if mode.startswith("stt"):
        if video_duration_sec > 0 and audio_duration > 0 and audio_duration < video_duration_sec * 0.95:
            issues.append(f"音频时长偏短：audio={audio_duration:.2f}s < video={video_duration_sec:.2f}s")
        if audio_duration > 0 and stt_duration > 0 and stt_duration < audio_duration * 0.95:
            issues.append(f"STT 覆盖时长偏短：stt={stt_duration:.2f}s < audio={audio_duration:.2f}s")
        min_chars = max(120, int((video_duration_sec or audio_duration or 0) * 0.35))
        if transcript_chars < min_chars:
            issues.append(f"转录文本偏短：chars={transcript_chars} < expected_min={min_chars}")
        if segment_count <= 1 and (video_duration_sec or audio_duration) >= 60:
            issues.append(f"分段数异常少：segment_count={segment_count}")
        if failed_segments:
            issues.append(f"存在失败分段：failed_segments={len(failed_segments)}")

    if degraded_reasons:
        status = "degraded"
    elif issues:
        status = "partial"
    return status, degraded_reasons + issues


def require_python_module(module_name: str):
    try:
        __import__(module_name)
    except Exception as exc:
        raise SystemExit(f"Missing or unusable Python dependency: {module_name} ({exc})")


def print_result_summary(info: dict, source_meta: dict, transcript_path: Path, request_path: Path | None, total_elapsed: float) -> None:
    lines = [
        "[result]",
        f"  title={info.get('title', 'Unknown')}",
        f"  platform={info.get('platform', 'unknown')}",
        f"  author={info.get('author', 'Unknown')}",
        f"  duration_sec={round(float(info.get('duration') or 0), 2)}",
        f"  source_mode={source_meta.get('mode', 'unknown')}",
        f"  final_status={source_meta.get('final_status', 'unknown')}",
        f"  audio_duration_sec={source_meta.get('audio_duration_sec', 0)}",
        f"  stt_duration_sec={source_meta.get('duration_sec', 0)}",
        f"  covered_duration_sec={source_meta.get('covered_duration_sec', 0)}",
        f"  audio_segment_count={source_meta.get('audio_segment_count', 0)}",
        f"  transcript_path={transcript_path}",
        f"  cleaned_transcript_path={source_meta.get('cleaned_transcript_path', '')}",
        f"  summary_path={source_meta.get('summary_path', '')}",
        f"  request_path={request_path or ''}",
        f"  total_elapsed_sec={round(total_elapsed, 2)}",
        f"  stt_preset={source_meta.get('stt_preset', 'default')}",
    ]
    stt_params = source_meta.get("stt_params") or {}
    if stt_params:
        lines.append(f"  stt_params={json.dumps(stt_params, ensure_ascii=False)}")
    audit_notes = source_meta.get("stt_audit_notes") or []
    for note in audit_notes:
        lines.append(f"  stt_audit={note}")
    eprint("\n".join(lines))
    issues = source_meta.get("quality_issues") or []
    for item in issues:
        warn(item)


def main():
    parser = argparse.ArgumentParser(description="Bilibili-first video summary helper")
    parser.add_argument("input")
    parser.add_argument("--chapter", action="store_true")
    parser.add_argument("--subtitle", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--lang", default="zh.*,zh-CN,zh-Hans,en.*")
    parser.add_argument("--transcribe", action="store_true", help="Legacy alias for --force-stt")
    parser.add_argument("--force-stt", action="store_true", help="Ignore subtitles and transcribe from audio directly")
    parser.add_argument("--keep-audio", action="store_true", help="Keep downloaded/transcode audio for debugging")
    parser.add_argument("--debug", action="store_true", help="Print detailed debug logs, commands, and STT parameters")
    parser.add_argument("--stt-language", default=DEFAULT_STT_LANGUAGE, help="Language hint for faster-whisper (default: zh)")
    parser.add_argument("--beam-size", type=int, default=DEFAULT_STT_BEAM_SIZE, help="Beam size for faster-whisper decoding")
    parser.add_argument("--no-vad", action="store_true", help="Disable VAD filter during STT for debugging")
    parser.add_argument("--segment-seconds", type=int, default=DEFAULT_SEGMENT_SECONDS, help="Split long audio into chunks of N seconds before STT (0 disables splitting)")
    parser.add_argument("--low-memory", action="store_true", help="Apply a safer faster-whisper preset for low-memory Windows machines")
    parser.add_argument("--resume", action="store_true", help="Reuse the latest saved transcript when safe, or rerun STT from saved audio when used with --force-stt")
    parser.add_argument("--verify", action="store_true", help="Verify the latest saved artifacts for this video without rerunning subtitle/STT extraction")
    parser.add_argument("--summary-mode", choices=["short", "standard", "technical"], default="standard", help="Generate a direct summary artifact in the selected mode")
    parser.add_argument("--print-result", action="store_true", default=True, help="Print the final artifact paths and summary status block")
    parser.add_argument("--output")
    parser.add_argument("--cookies")
    parser.add_argument("--cookies-from-browser", help="Pass-through value for yt-dlp, e.g. edge or edge:Default")
    parser.add_argument("--project-root", default=PROJECT_ROOT_ENV)
    parser.add_argument("--stt-model", default=WHISPER_MODEL)
    parser.add_argument("--clear-cache", action="store_true", help="Clear the configured project cache directory and exit")
    parser.add_argument("--clear-temp", action="store_true", help="Clear the configured project temp directory and exit")
    args = parser.parse_args()

    started = time.perf_counter()
    global DEBUG
    DEBUG = args.debug
    args.input = normalize_input(args.input)
    args.force_stt = args.force_stt or args.transcribe
    args.vad_filter = not args.no_vad
    args.stt_preset = "default"
    apply_stt_preset(args)
    args.project_root = resolve_project_root(args.project_root)
    dirs = ensure_project_dirs(Path(args.project_root))

    if args.clear_cache or args.clear_temp:
        cleanup_paths(dirs, args.clear_cache, args.clear_temp)
        print(json.dumps({"projectRoot": str(dirs['root']), "clearCache": args.clear_cache, "clearTemp": args.clear_temp}, ensure_ascii=False))
        return

    with StageTimer("依赖检查"):
        require_cmds("python", "ffmpeg", "ffprobe")
        require_python_module("faster_whisper")
        if not normalize_text(HF_ENDPOINT_ENV, default=INIT_DEFAULTS["hfEndpoint"]):
            raise SystemExit("HF endpoint 配置为空，且无法回退到默认值。请先运行 init.py 修正配置。")

    with StageTimer("解析输入与视频信息"):
        platform = detect_platform(args.input)
        info = {"title": Path(args.input).name if platform == "local" else "Unknown", "duration": 0.0, "author": "Unknown", "platform": platform}
        if platform == "local":
            info["duration"] = ffprobe_duration(args.input)
            info["author"] = "Local File"
        else:
            try:
                require_cmds("yt-dlp")
                info = get_video_info(args.input, args.cookies, args.cookies_from_browser)
            except Exception as exc:
                info["platform"] = platform
                warn(f"获取视频元信息失败，将继续执行：{exc}")

    video_id = extract_video_id(args.input, platform)
    display_title = info.get("title") or args.input
    task_prefix = artifact_prefix(platform, video_id)
    task_slug = slugify("-".join([task_prefix, str(int(time.time()))]))

    log_kv(title=display_title, bv=video_id or "", duration_sec=round(float(info.get("duration") or 0), 2), source_mode=("stt-forced" if args.force_stt else "subtitle-auto"))
    debug_log(f"project_root={dirs['root']} hf_cache={dirs['hf']}")
    debug_log(f"cli stt settings language={args.stt_language} beam_size={args.beam_size} vad_filter={args.vad_filter} segment_seconds={args.segment_seconds} preset={args.stt_preset}")

    latest_meta_path = find_latest_run_metadata(dirs, task_prefix)
    latest_run = load_json(latest_meta_path) if latest_meta_path and latest_meta_path.exists() else None
    transcript = ""
    source_meta: dict = {"mode": "unknown", "stt_audit_notes": build_stt_audit_notes(args)}
    transcript_path = None

    if args.verify:
        if not latest_run:
            raise SystemExit(f"未找到可验证的历史记录：prefix={task_prefix}")
        verify_status, verify_issues = verify_run_artifacts(latest_run)
        print(json.dumps({
            "taskSlug": latest_run.get("taskSlug", ""),
            "status": verify_status,
            "issues": verify_issues,
            "transcriptPath": latest_run.get("transcriptPath", ""),
            "requestPath": latest_run.get("requestPath", ""),
            "metadataPath": str(latest_meta_path),
        }, ensure_ascii=False, indent=2))
        return

    if args.resume and args.force_stt:
        resume_audio = find_latest_downloaded_audio(dirs, task_prefix)
        if resume_audio and resume_audio.exists():
            with StageTimer("恢复历史音频并执行 STT"):
                transcript, source_meta = transcribe_audio_path(
                    resume_audio,
                    dirs,
                    args.stt_model,
                    True,
                    "stt-resume",
                    resume_audio.parent,
                    language=args.stt_language,
                    vad_filter=args.vad_filter,
                    beam_size=args.beam_size,
                    segment_seconds=args.segment_seconds,
                )
                source_meta["resumed_from_audio"] = str(resume_audio)
        else:
            warn("--resume 与 --force-stt 同时使用时未找到可复用音频，将重新下载音频并执行 STT。")
    elif args.resume and latest_run and latest_run.get("transcriptPath") and Path(latest_run["transcriptPath"]).exists():
        with StageTimer("恢复历史 transcript"):
            transcript_path = Path(latest_run["transcriptPath"])
            transcript = transcript_path.read_text(encoding="utf-8")
            source_meta = latest_run.get("source") or {"mode": "resume"}
            display_title = latest_run.get("title") or display_title
            info["title"] = display_title
            info["author"] = latest_run.get("author") or info.get("author", "Unknown")
            info["duration"] = latest_run.get("durationSeconds") or info.get("duration", 0.0)
            source_meta["resumed_from"] = str(latest_meta_path)
    elif args.resume:
        resume_audio = find_latest_downloaded_audio(dirs, task_prefix)
        if resume_audio and resume_audio.exists():
            with StageTimer("恢复历史音频并执行 STT"):
                transcript, source_meta = transcribe_audio_path(resume_audio, dirs, args.stt_model, True, "stt-resume", resume_audio.parent, language=args.stt_language, vad_filter=args.vad_filter, beam_size=args.beam_size, segment_seconds=args.segment_seconds)
                source_meta["resumed_from_audio"] = str(resume_audio)

    if not transcript and platform == "bilibili" and not args.force_stt:
        transcript, source_meta = extract_bilibili_subtitles_with_yutto(args.input, dirs, task_slug)

    if not transcript and platform != "bilibili" and not args.force_stt:
        transcript = extract_subtitles_generic(args.input, args.lang, args.cookies, args.cookies_from_browser)
        if transcript:
            source_meta = {"mode": "subtitle-auto", "provider": "yt-dlp"}

    if not transcript:
        eprint("[info] 未拿到可用字幕，转入 STT。" if not args.force_stt else "[info] 已指定 --force-stt，直接进入 STT。")
        if platform == "bilibili":
            forced_mode = "stt-forced" if args.force_stt else "stt-fallback"
            transcript, source_meta = transcribe_bilibili_with_faster_whisper(args.input, dirs, task_slug, args.stt_model, args.keep_audio, args.stt_language, args.vad_filter, args.beam_size, args.segment_seconds)
            source_meta["mode"] = forced_mode
        elif platform == "local":
            forced_mode = "stt-forced" if args.force_stt else "stt-fallback"
            transcript, source_meta = transcribe_local_file_with_faster_whisper(args.input, dirs, args.stt_model, args.keep_audio, args.stt_language, args.vad_filter, args.beam_size, args.segment_seconds)
            source_meta["mode"] = forced_mode
        else:
            require_cmds("yt-dlp")
            forced_mode = "stt-forced" if args.force_stt else "stt-fallback"
            transcript, source_meta = transcribe_remote_with_yt_dlp(args.input, args.cookies, args.cookies_from_browser, dirs, args.stt_model, args.keep_audio, task_slug, forced_mode, args.stt_language, args.vad_filter, args.beam_size, args.segment_seconds)

    if not transcript:
        raise SystemExit("无法提取字幕，也无法完成 STT 转录。")

    with StageTimer("保存 transcript"):
        transcript = clean_text(transcript)
        if not transcript_path:
            transcript_path = save_text(dirs["transcripts"] / f"{task_slug}.txt", transcript)
        cleaned_transcript = build_cleaned_transcript(transcript)
        cleaned_transcript_path = save_text(dirs["cleaned_transcripts"] / f"{task_slug}.txt", cleaned_transcript)
        source_meta["cleaned_transcript_path"] = str(cleaned_transcript_path)

    final_status, quality_issues = assess_transcript_quality(transcript, float(info.get("duration") or 0), source_meta)
    source_meta["quality_issues"] = quality_issues
    source_meta["final_status"] = final_status

    source_meta["stt_preset"] = args.stt_preset
    summary_path = save_text(
        dirs["summaries"] / f"{task_slug}-{args.summary_mode}.md",
        build_summary_text(transcript, display_title, info.get("author", "Unknown"), info.get("duration", 0.0), source_meta, args.summary_mode),
    )
    source_meta["summary_path"] = str(summary_path)
    source_meta["summary_mode"] = args.summary_mode

    output = transcript if args.subtitle else build_request(
        transcript,
        display_title,
        platform,
        info.get("author", "Unknown"),
        info.get("duration", 0.0),
        args.chapter,
        args.json,
        transcript_path,
        source_meta,
    )

    request_path = None
    with StageTimer("生成 request/输出"):
        if args.output:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(output, encoding="utf-8")
            print(out_path)
            request_path = out_path
        else:
            request_path = save_text(dirs["requests"] / f"{task_slug}.{'json' if args.json else 'md'}", output)
            print(output)
            eprint(f"transcript saved: {transcript_path}")
            eprint(f"request saved: {request_path}")

    total_elapsed = time.perf_counter() - started
    metadata_path = dirs["metadata"] / f"{task_slug}.json"
    cleaned_transcript_path = Path(source_meta.get("cleaned_transcript_path", "")) if source_meta.get("cleaned_transcript_path") else None
    summary_path = Path(source_meta.get("summary_path", "")) if source_meta.get("summary_path") else None
    run_record = build_run_record(task_slug, args.input, info, source_meta, transcript_path, cleaned_transcript_path, summary_path, request_path, metadata_path, total_elapsed, args)
    save_json(metadata_path, run_record)
    append_jsonl(dirs["outputs"] / "index.jsonl", run_record)
    if args.print_result:
        print_result_summary(info, source_meta, transcript_path, request_path, total_elapsed)
    eprint(f"metadata saved: {metadata_path}")


if __name__ == "__main__":
    main()
