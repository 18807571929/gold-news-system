"""项目路径解析：支持 config.yaml 与 GOLD_DATA_ROOT 环境变量。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

DEFAULT_HISTORY_DATA = Path("data/archive")
DEFAULT_BACKTEST_OUTPUT = Path("data/backtest")


def load_config(config_path: str | Path = "config/config.yaml") -> dict[str, Any]:
    with Path(config_path).open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_path(value: str | Path | None, env_key: str, default: Path) -> Path:
    env_val = os.environ.get(env_key, "").strip()
    if env_val:
        return Path(env_val).expanduser()
    if value:
        return Path(value).expanduser()
    return default


def get_history_data_root(config: dict[str, Any] | None = None) -> Path:
    cfg = config or load_config()
    paths = cfg.get("paths", {})
    return resolve_path(
        paths.get("history_data"),
        "GOLD_DATA_ROOT",
        DEFAULT_HISTORY_DATA,
    )


def get_backtest_output_root(config: dict[str, Any] | None = None) -> Path:
    cfg = config or load_config()
    paths = cfg.get("paths", {})
    return resolve_path(
        paths.get("backtest_output"),
        "GOLD_BACKTEST_ROOT",
        DEFAULT_BACKTEST_OUTPUT,
    )


def get_strategy_root(config: dict[str, Any] | None = None) -> Path:
    """golden_shield_v1.7 策略目录（config / GS_STRATEGY_ROOT / 默认 sibling）。"""
    cfg = config or load_config()
    paths = cfg.get("paths", {})
    backtest_root = get_backtest_output_root(cfg)
    default = (backtest_root.parent / "golden_shield_v1.7").resolve()
    raw = paths.get("strategy_root")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raw = None
    return resolve_path(raw, "GS_STRATEGY_ROOT", default)


def ensure_history_dirs(root: Path | None = None) -> dict[str, Path]:
    base = root or get_history_data_root()
    dirs = {
        "root": base,
        "news": base / "news",
        "sentiment": base / "sentiment",
        "signals": base / "signals",
        "state": base / "state",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs
