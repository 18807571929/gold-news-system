"""将 golden_shield_v1.7 策略源码恢复到 E:\\量化项目\\golden_shield_v1.7 并写入 config。"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.paths import get_backtest_output_root, get_strategy_root, load_config  # noqa: E402

REQUIRED_FILES = (
    "config.py",
    "grid_manager.py",
    "strategy.py",
    "golden_shield_trend_grid.py",
    "model_profile.py",
    "indicators.py",
    "position_manager.py",
    "risk_manager.py",
)

DEFAULT_SOURCES = [
    Path(r"D:\OneDrive\35 币圈\11 XAUUSD\golden_shield_v1.7"),
    Path(r"\\Rhino-workshop\d\OneDrive\35 币圈\11 XAUUSD\golden_shield_v1.7"),
    Path(r"E:\OneDrive\35 币圈\11 XAUUSD\golden_shield_v1.7"),
    Path(r"E:\量化项目\golden_shield_v1.6"),  # 临时降级：v1.6 可跑部分 v17 脚本
]


def _validate_strategy(root: Path) -> list[str]:
    missing = [name for name in REQUIRED_FILES if not (root / name).is_file()]
    return missing


def _discover_source(explicit: Path | None) -> Path | None:
    if explicit and explicit.is_dir():
        return explicit.resolve()
    env = os.environ.get("GS_STRATEGY_SOURCE", "").strip()
    if env and Path(env).is_dir():
        return Path(env).resolve()
    for candidate in DEFAULT_SOURCES:
        if candidate.is_dir() and not _validate_strategy(candidate):
            return candidate.resolve()
    return None


def _copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        backup = dst.with_name(dst.name + "_backup")
        if backup.exists():
            shutil.rmtree(backup)
        dst.rename(backup)
        print(f"已备份旧目录 → {backup}")
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git", "backtests", "backtest_results"),
    )


def _patch_config(strategy_root: Path) -> None:
    cfg_path = PROJECT_ROOT / "config" / "config.yaml"
    text = cfg_path.read_text(encoding="utf-8")
    line = f'  strategy_root: "{strategy_root.as_posix()}"'
    if "strategy_root:" in text:
        out_lines = []
        for ln in text.splitlines():
            if ln.strip().startswith("strategy_root:"):
                out_lines.append(line)
            else:
                out_lines.append(ln)
        cfg_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    else:
        cfg_path.write_text(text.rstrip() + "\n" + line + "\n", encoding="utf-8")
    print(f"已更新 config.yaml strategy_root → {strategy_root}")


def _import_smoke(strategy_root: Path, backtest_root: Path) -> bool:
    sys.path.insert(0, str(strategy_root))
    sys.path.insert(0, str(backtest_root))
    try:
        import config as cfg  # noqa: F401
        import grid_manager as gm  # noqa: F401
        import golden_shield_trend_grid as gsg  # noqa: F401
        from gs_v17.bootstrap import ensure_paths  # noqa: F401

        ensure_paths()
        print("导入检查通过: config, grid_manager, golden_shield_trend_grid, gs_v17")
        return True
    except Exception as exc:
        print(f"导入检查失败: {exc}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="恢复 golden_shield_v1.7 策略目录")
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="源目录（如 OneDrive 同步后的 golden_shield_v1.7 路径）",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help="目标目录，默认 E:/量化项目/golden_shield_v1.7",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅检测，不复制")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    config = load_config(PROJECT_ROOT / "config/config.yaml")
    backtest_root = get_backtest_output_root(config)
    target = (args.target or get_strategy_root(config)).resolve()

    src = _discover_source(args.source)
    print(f"目标: {target}")
    if target.is_dir() and not _validate_strategy(target):
        print("目标已存在且文件完整，无需恢复。")
        _patch_config(target)
        ok = _import_smoke(target, backtest_root)
        return 0 if ok else 1

    if src is None:
        print("\n未找到可用的 golden_shield 源目录。")
        print("请从旧电脑（Rhino-workshop）或 OneDrive 复制后执行：")
        print('  python scripts/restore_golden_shield_v17.py --source "D:\\...\\golden_shield_v1.7"')
        print("\n历史路径（README_gs_v17.md）：")
        print(r"  D:\OneDrive\35 币圈\11 XAUUSD\golden_shield_v1.7")
        print("\n已尝试的默认路径：")
        for p in DEFAULT_SOURCES:
            status = "OK" if p.is_dir() else "MISS"
            miss = _validate_strategy(p) if p.is_dir() else REQUIRED_FILES
            print(f"  [{status}] {p}" + (f" 缺: {miss[:3]}..." if p.is_dir() and miss else ""))
        return 1

    missing = _validate_strategy(src)
    if missing:
        print(f"源目录不完整，缺少: {missing}")
        return 1

    print(f"源: {src}")
    if args.dry_run:
        print("dry-run: 将复制到", target)
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    _copy_tree(src, target)
    _patch_config(target)
    ok = _import_smoke(target, backtest_root)
    if ok:
        print("\n恢复完成。下一步：")
        print("  python scripts/run_gs_v17_news_backtest.py --smoke")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
