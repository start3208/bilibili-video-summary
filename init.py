#!/usr/bin/env python
import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SKILL_DIR / "init.json"
DEFAULTS = {
    "initialized": False,
    "projectRoot": "",
    "sttModel": "small",
    "hfEndpoint": "https://hf-mirror.com",
    "hfHome": "",
    "updatedAt": None,
}
DEPENDENCY_NAMES = ("ffmpeg", "ffprobe", "yt-dlp")
SUPPORTED_STT_MODELS = [
    "tiny",
    "base",
    "small",
    "medium",
    "large-v1",
    "large-v2",
    "large-v3",
    "large-v3-turbo",
    "large",
    "turbo",
]


def normalize_text(value, *, default=None, keep_blank=False):
    if value is None:
        return "" if keep_blank else default
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return "" if keep_blank else default
        return stripped
    return value


def normalize_config(data):
    merged = dict(DEFAULTS)
    if isinstance(data, dict):
        merged.update(data)
    merged["projectRoot"] = normalize_text(merged.get("projectRoot"), default="", keep_blank=True)
    merged["sttModel"] = normalize_text(merged.get("sttModel"), default=DEFAULTS["sttModel"])
    merged["hfEndpoint"] = normalize_text(merged.get("hfEndpoint"), default=DEFAULTS["hfEndpoint"])
    merged["hfHome"] = normalize_text(merged.get("hfHome"), default="", keep_blank=True)
    merged["initialized"] = bool(merged.get("initialized")) and bool(merged["projectRoot"])
    merged["updatedAt"] = merged.get("updatedAt") or None
    return merged


def load_config():
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return normalize_config(data)
        except Exception:
            pass
    return normalize_config({})


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(normalize_config(cfg), ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_project_dirs(project_root: Path):
    outputs = project_root / "outputs"
    for p in [
        project_root,
        project_root / "models",
        project_root / "temp",
        project_root / "downloads",
        outputs,
        outputs / "transcripts",
        outputs / "cleaned-transcripts",
        outputs / "requests",
        outputs / "summaries",
        outputs / "metadata",
        project_root / "cache",
        project_root / "cache" / "huggingface",
        project_root / "logs",
    ]:
        p.mkdir(parents=True, exist_ok=True)
    (outputs / "index.jsonl").touch(exist_ok=True)


def run_check(cmd):
    try:
        proc = subprocess.run(cmd, check=True, text=True, capture_output=True, encoding="utf-8", errors="replace")
        return True, (proc.stdout or proc.stderr).strip()
    except Exception as exc:
        return False, str(exc)


def version_args(name):
    return ["--version"] if name == "yt-dlp" else ["-version"]


def collect_dependency_checks():
    checks = {}
    for name in DEPENDENCY_NAMES:
        resolved = shutil.which(name)
        ok = False
        detail = "not found"
        if resolved:
            ok, detail = run_check([name, *version_args(name)])
        checks[name] = {
            "path": resolved or "",
            "ok": ok,
            "detail": detail.splitlines()[0] if detail else "",
        }

    ok, detail = run_check([sys.executable, "-c", "import faster_whisper; print('ok')"])
    checks["faster_whisper"] = {
        "ok": ok,
        "detail": detail.splitlines()[-1] if detail else "",
    }
    return checks


def collect_config_checks(cfg):
    project_root = Path(cfg["projectRoot"]) if cfg.get("projectRoot") else None
    hf_home_value = cfg.get("hfHome", "")
    return {
        "projectRootConfigured": bool(cfg.get("projectRoot")),
        "projectRootExists": project_root.exists() if project_root else False,
        "sttModelConfigured": bool(cfg.get("sttModel")),
        "hfEndpointConfigured": bool(cfg.get("hfEndpoint")),
        "hfHomeConfigured": bool(hf_home_value),
        "hfHomeUsesProjectCache": not bool(hf_home_value),
        "configPath": str(CONFIG_PATH),
    }


def build_ai_advice(cfg, missing_dependencies):
    return {
        "summary": "初始化未完成。请先向用户确认配置字段，再安装缺失依赖并重新运行 init.py --init。",
        "confirmWithUser": {
            "initialized": cfg.get("initialized", False),
            "projectRoot": cfg.get("projectRoot", ""),
            "sttModel": cfg.get("sttModel", ""),
            "hfEndpoint": cfg.get("hfEndpoint", ""),
            "hfHome": cfg.get("hfHome", ""),
        },
        "installDependencies": missing_dependencies,
    }


def collect_status(cfg):
    cfg = normalize_config(cfg)
    warnings = []
    checks = collect_config_checks(cfg)
    dependency_checks = collect_dependency_checks()
    checks.update(dependency_checks)

    missing_dependencies = [name for name, item in dependency_checks.items() if not item.get("ok")]
    config_ready = all([
        checks["projectRootConfigured"],
        checks["projectRootExists"],
        checks["sttModelConfigured"],
        checks["hfEndpointConfigured"],
    ])
    dependencies_ready = not missing_dependencies
    effective_initialized = bool(cfg.get("initialized")) and config_ready and dependencies_ready

    normalized_cfg = dict(cfg)
    normalized_cfg["initialized"] = effective_initialized

    checks["configReady"] = config_ready
    checks["dependenciesReady"] = dependencies_ready
    checks["missingDependencies"] = missing_dependencies

    if not normalized_cfg.get("initialized"):
        warnings.append("尚未完成初始化。请运行 init.py --init，并确认 initialized、projectRoot、sttModel、hfEndpoint、hfHome。")
    if not normalized_cfg.get("projectRoot"):
        warnings.append("projectRoot 未设置。交互式初始化会询问用户，不再默认写入 F 盘。")
    elif not checks["projectRootExists"]:
        warnings.append("projectRoot 指向的目录不存在，请确认目录路径是否正确。")
    if not normalized_cfg.get("hfEndpoint"):
        warnings.append("hfEndpoint 为空，将回退到默认值。")
    if missing_dependencies:
        warnings.append("存在未安装或不可用的依赖，请先安装相关依赖后再完成初始化。")
    if cfg.get("initialized") and not effective_initialized:
        warnings.append("检测到 initialized=true 但当前检查未通过，已按检查结果将其视为 false。")

    ai_advice = build_ai_advice(normalized_cfg, missing_dependencies) if not effective_initialized else None
    return {"config": normalized_cfg, "warnings": warnings, "checks": checks, "aiAdvice": ai_advice}


def collect_status_and_persist(cfg):
    status = collect_status(cfg)
    if normalize_config(cfg) != status["config"]:
        updated_cfg = dict(status["config"])
        updated_cfg["updatedAt"] = datetime.now().isoformat(timespec="seconds")
        save_config(updated_cfg)
        status["config"] = normalize_config(updated_cfg)
    return status


def should_prompt(value):
    return value is None


def prompt_value(enabled, text, current, *, keep_blank=False):
    if not enabled:
        return current
    raw = input(text).strip()
    if raw == "":
        return "" if keep_blank else current
    return raw


def prompt_required_value(enabled, text, current):
    value = current
    while True:
        value = prompt_value(enabled, text, value, keep_blank=False)
        value = normalize_text(value, default="", keep_blank=True)
        if value:
            return value
        if not enabled:
            return value
        print("project root 不能为空，请输入一个目录。", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Initialize bilibili-video-summary settings")
    parser.add_argument("--status", action="store_true", help="Show current initialization status")
    parser.add_argument("--init", action="store_true", help="Initialize or update config")
    parser.add_argument("--non-interactive", action="store_true", help="Never prompt; use provided args plus normalized defaults/config")
    parser.add_argument("--project-root")
    parser.add_argument("--stt-model", choices=SUPPORTED_STT_MODELS)
    parser.add_argument("--hf-endpoint")
    parser.add_argument("--hf-home")
    args = parser.parse_args()

    cfg = load_config()

    if args.status and not args.init:
        print(json.dumps(collect_status_and_persist(cfg), ensure_ascii=False, indent=2))
        return

    if args.init or not any([args.status, args.init]):
        interactive = sys.stdin.isatty() and not args.non_interactive
        project_root = normalize_text(args.project_root, default=cfg["projectRoot"], keep_blank=True)
        stt_model = normalize_text(args.stt_model, default=cfg["sttModel"])
        hf_endpoint = normalize_text(args.hf_endpoint, default=cfg["hfEndpoint"])
        hf_home = normalize_text(args.hf_home, default=cfg["hfHome"], keep_blank=True)

        if interactive and should_prompt(args.project_root):
            if cfg["projectRoot"]:
                project_root = prompt_required_value(True, f"project root [{cfg['projectRoot']}]: ", project_root)
            else:
                project_root = prompt_required_value(True, "project root (required): ", project_root)
        if interactive and should_prompt(args.stt_model):
            choices_text = "/".join(SUPPORTED_STT_MODELS)
            stt_model = prompt_value(True, f"stt model ({choices_text}) [{cfg['sttModel']}]: ", stt_model)
        if interactive and should_prompt(args.hf_endpoint):
            hf_endpoint = prompt_value(True, f"hf endpoint [{cfg['hfEndpoint']}]: ", hf_endpoint)
        if interactive and should_prompt(args.hf_home):
            hf_home = prompt_value(True, f"hf home (blank=project cache) [{cfg['hfHome']}]: ", hf_home, keep_blank=True)

        project_root = normalize_text(project_root, default="", keep_blank=True)
        if not project_root:
            raise SystemExit("projectRoot 未配置。请重新运行 init.py --init 并输入目录，或使用 --project-root 显式传入。")

        cfg.update({
            "initialized": True,
            "projectRoot": project_root,
            "sttModel": normalize_text(stt_model, default=DEFAULTS["sttModel"]),
            "hfEndpoint": normalize_text(hf_endpoint, default=DEFAULTS["hfEndpoint"]),
            "hfHome": normalize_text(hf_home, default="", keep_blank=True),
            "updatedAt": datetime.now().isoformat(timespec="seconds"),
        })
        cfg = normalize_config(cfg)
        ensure_project_dirs(Path(cfg["projectRoot"]))
        save_config(cfg)
        print(json.dumps(collect_status_and_persist(cfg), ensure_ascii=False, indent=2))
        return


if __name__ == "__main__":
    main()
