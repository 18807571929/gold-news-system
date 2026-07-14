"""将 gold-news-system JSON 缓存同步到 History Data Parquet 归档。"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.paths import ensure_history_dirs, get_history_data_root, load_config  # noqa: E402

CHINA_TZ = timezone(timedelta(hours=8))


def _collect_json_records(source_dir: Path, dataset: str) -> list[dict]:
    if not source_dir.is_dir():
        return []
    records: list[dict] = []
    for json_path in sorted(source_dir.rglob("*.json")):
        if json_path.name in {"seen_urls.json", "processed_ids.json", "executed_ids.json"}:
            continue
        try:
            with json_path.open(encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        record_id = str(data.get("id") or data.get("news_id") or json_path.stem)
        data["_record_id"] = record_id
        data["_source_file"] = str(json_path.relative_to(PROJECT_ROOT))
        data["_dataset"] = dataset
        data["_synced_at"] = datetime.now(CHINA_TZ).isoformat()
        records.append(data)
    return records


def _init_state_db(state_dir: Path) -> sqlite3.Connection:
    db_path = state_dir / "sync_state.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_log (
            dataset TEXT NOT NULL,
            record_id TEXT NOT NULL,
            synced_at TEXT NOT NULL,
            PRIMARY KEY (dataset, record_id)
        )
        """
    )
    conn.commit()
    return conn


def _merge_parquet(out_path: Path, records: list[dict], id_col: str = "_record_id") -> int:
    if not records:
        return 0
    new_df = pd.json_normalize(records)
    if out_path.exists():
        old_df = pd.read_parquet(out_path)
        merged = pd.concat([old_df, new_df], ignore_index=True)
        merged = merged.drop_duplicates(subset=[id_col], keep="last")
    else:
        merged = new_df.drop_duplicates(subset=[id_col], keep="last")
    merged.to_parquet(out_path, index=False)
    return len(new_df)


def sync_dataset(
    name: str,
    source_dir: Path,
    out_path: Path,
    conn: sqlite3.Connection,
) -> dict[str, int]:
    records = _collect_json_records(source_dir, name)
    if not records:
        return {"files": 0, "records": 0, "merged": 0}

    new_count = _merge_parquet(out_path, records)
    now = datetime.now(CHINA_TZ).isoformat()
    for rec in records:
        conn.execute(
            "INSERT OR REPLACE INTO sync_log(dataset, record_id, synced_at) VALUES (?, ?, ?)",
            (name, rec["_record_id"], now),
        )
    conn.commit()
    return {"files": len(records), "records": len(records), "merged": new_count}


def main() -> int:
    parser = argparse.ArgumentParser(description="同步 JSON 缓存到 Parquet 归档")
    parser.add_argument("--config", default="config/config.yaml")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    config = load_config(PROJECT_ROOT / args.config)
    dirs = ensure_history_dirs(get_history_data_root(config))
    conn = _init_state_db(dirs["state"])

    mappings = {
        "news": (PROJECT_ROOT / "data" / "news_cache", dirs["news"] / "jin10_news.parquet"),
        "sentiment": (PROJECT_ROOT / "data" / "sentiment_cache", dirs["sentiment"] / "sentiment.parquet"),
        "signals": (PROJECT_ROOT / "data" / "signals", dirs["signals"] / "signals.parquet"),
    }

    summary: dict[str, dict[str, int]] = {}
    for name, (src, dst) in mappings.items():
        summary[name] = sync_dataset(name, src, dst, conn)

    conn.close()

    print(json.dumps({"history_data_root": str(dirs["root"]), "datasets": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
