"""Shared configuration, manifest, and logging helpers for the pipeline."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ConfigError(ValueError):
    """Raised when the project configuration is invalid."""


def load_config(path: str | os.PathLike[str]) -> dict[str, Any]:
    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise ConfigError(f"Config file does not exist: {config_path}")
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Config file is not valid YAML: {exc}") from exc
    if not isinstance(config, dict):
        raise ConfigError("Config root must be a mapping")
    validate_config(config, config_path.parent)
    return config


def validate_config(config: dict[str, Any], base_dir: Path = ROOT) -> None:
    required = {
        "api": ("base_url", "translation_id", "transliteration_id", "text_edition", "riwaya"),
        "reciter": ("name_ar",),
        "matching": ("repeat_window", "repeat_mode", "min_match_score", "low_conf_score"),
        "alignment": ("silence_snap_max_s", "silence_threshold_db", "trim_last_word_silence",
                      "min_word_conf", "max_ayah_seconds_factor"),
        "models": ("recognizer", "aligner", "device"),
        "display": ("header_title", "group_min_font_size", "min_font_size", "max_font_size",
                    "max_lines", "max_ayahs_per_screen", "lead_time_s", "reciter_font",
                    "arabic_font", "translit_font", "english_font", "boxes"),
        "paths": ("background", "template_ass", "work_dir", "output_dir", "log_dir"),
    }
    for section, keys in required.items():
        value = config.get(section)
        if not isinstance(value, dict):
            raise ConfigError(f"Missing config section: {section}")
        missing = [key for key in keys if key not in value]
        if missing:
            raise ConfigError(f"Missing config keys in {section}: {', '.join(missing)}")
    if config["api"]["riwaya"] != "hafs":
        raise ConfigError("api.riwaya must be 'hafs'")
    if config["matching"]["repeat_mode"] not in {"last", "highest_confidence"}:
        raise ConfigError("matching.repeat_mode must be 'last' or 'highest_confidence'")
    if config["display"]["header_title"] not in {"arabic_only", "arabic_and_english"}:
        raise ConfigError("display.header_title must be 'arabic_only' or 'arabic_and_english'")
    if not isinstance(config["bismillah"], bool):
        raise ConfigError("bismillah must be true or false")
    _require_number(config["matching"], "repeat_window", 1)
    for key in ("min_match_score", "low_conf_score"):
        _require_number(config["matching"], key, 0, 1)
    for key in ("min_word_conf", "max_ayah_seconds_factor"):
        _require_number(config["alignment"], key, 0)
    _require_number(config["alignment"], "silence_snap_max_s", 0)
    for key in ("min_font_size", "group_min_font_size", "max_font_size", "max_lines",
                "max_ayahs_per_screen"):
        _require_number(config["display"], key, 1)
    if config["display"]["min_font_size"] > config["display"]["group_min_font_size"]:
        raise ConfigError("display.min_font_size cannot exceed group_min_font_size")
    if config["display"]["group_min_font_size"] > config["display"]["max_font_size"]:
        raise ConfigError("display.group_min_font_size cannot exceed max_font_size")
    _require_number(config["display"], "lead_time_s", 0)
    if not isinstance(config["display"]["boxes"], dict):
        raise ConfigError("display.boxes must be a mapping")
    for box_name in ("arabic", "translit", "english", "reciter", "title"):
        box = config["display"]["boxes"].get(box_name)
        if not isinstance(box, list) or len(box) != 4 or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) for value in box
        ):
            raise ConfigError(f"display.boxes.{box_name} must contain four numbers")
    for key in ("background", "template_ass"):
        path = resolve_path(config["paths"][key], base_dir)
        if not path.is_file():
            raise ConfigError(f"Configured file does not exist: {path}")


def _require_number(values: dict[str, Any], key: str, minimum: float, maximum: float | None = None) -> None:
    value = values.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{key} must be a number")
    if value < minimum or (maximum is not None and value > maximum):
        raise ConfigError(f"{key} must be between {minimum} and {maximum}")


def resolve_path(value: str, base_dir: Path = ROOT) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else base_dir / path


def file_hash(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"stages": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot read manifest {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("stages", {}), dict):
        raise ConfigError(f"Invalid manifest structure: {path}")
    return data


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def stage_is_current(manifest: dict[str, Any], stage: str, inputs: Any, config: Any) -> bool:
    record = manifest.get("stages", {}).get(stage)
    return bool(record and record.get("inputs") == stable_hash(inputs)
                and record.get("config") == stable_hash(config))


def record_stage(manifest: dict[str, Any], stage: str, inputs: Any, config: Any, outputs: list[str]) -> None:
    manifest.setdefault("stages", {})[stage] = {
        "inputs": stable_hash(inputs), "config": stable_hash(config), "outputs": outputs,
    }


def configure_logging(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("quran_pipeline")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger
