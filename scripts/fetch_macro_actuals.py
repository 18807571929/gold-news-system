"""拉取 FRED 宏观序列并写入 history_root/macro/fred_releases.parquet。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.backtest_adapter.macro_actuals import build_fred_release_cache  # noqa: E402
from src.paths import get_history_data_root, load_config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="拉取 FRED 宏观发布缓存")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    config = load_config(PROJECT_ROOT / args.config)
    history_root = get_history_data_root(config)
    cache = build_fred_release_cache(history_root, force=args.force)
    summary = {
        "rows": len(cache),
        "categories": cache["category"].value_counts().to_dict() if not cache.empty else {},
        "path": str(history_root / "macro" / "fred_releases.parquet"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not cache.empty else 1


if __name__ == "__main__":
    raise SystemExit(main())
