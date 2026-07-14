"""回测适配器：加载信号/行情数据。"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

CHINA_TZ = timezone(timedelta(hours=8))


def _parse_signal_time(row: dict[str, Any]) -> datetime | None:
    for key in ("timestamp", "generated_at"):
        val = row.get(key)
        if val:
            return pd.to_datetime(val, utc=True).to_pydatetime()
    news = row.get("news_summary") or row.get("news") or {}
    t = news.get("time")
    if t:
        dt = pd.to_datetime(t)
        if dt.tzinfo is None:
            dt = dt.tz_localize(CHINA_TZ)
        return dt.to_pydatetime()
    return None


def load_signals_from_json(signals_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not signals_dir.is_dir():
        return records
    for path in sorted(signals_dir.rglob("*.json")):
        if path.name == "executed_ids.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        data["_source_file"] = str(path)
        records.append(data)
    return records


def load_signals_from_parquet(parquet_path: Path) -> list[dict[str, Any]]:
    if not parquet_path.is_file():
        return []
    df = pd.read_parquet(parquet_path)
    return df.to_dict(orient="records")


def load_signals(
    *,
    signals_dir: Path | None = None,
    parquet_path: Path | None = None,
    historical_path: Path | None = None,
    source: str = "merged",
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> list[dict[str, Any]]:
    """加载回测信号。source: live | historical | merged"""
    live: list[dict] = []
    hist: list[dict] = []

    if source in ("live", "merged"):
        if parquet_path and parquet_path.is_file():
            live = _normalize_signal_records(load_signals_from_parquet(parquet_path))
        elif signals_dir:
            live = _normalize_signal_records(load_signals_from_json(signals_dir))

    if source in ("historical", "merged") and historical_path and historical_path.is_file():
        from .historical_signals import load_historical_signals_parquet

        hist = _normalize_signal_records(load_historical_signals_parquet(historical_path))

    if source == "historical":
        records = hist
    elif source == "live":
        records = live
    else:
        seen: set[str] = set()
        records = []
        for rec in live + hist:
            nid = rec.get("news_id", "")
            if nid in seen:
                continue
            seen.add(nid)
            records.append(rec)
        records.sort(key=lambda x: x["timestamp"])

    if window_start is not None or window_end is not None:
        ws = pd.Timestamp(window_start).tz_convert("UTC") if window_start else None
        we = pd.Timestamp(window_end).tz_convert("UTC") if window_end else None
        filtered = []
        for rec in records:
            ts = pd.Timestamp(rec["timestamp"]).tz_convert("UTC")
            if ws is not None and ts < ws:
                continue
            if we is not None and ts > we:
                continue
            filtered.append(rec)
        records = filtered

    return records


def _unnest_parquet_row(row: dict[str, Any]) -> dict[str, Any]:
    """将 pd.json_normalize 扁平列还原为嵌套结构。"""
    if row.get("impact_analysis") and isinstance(row["impact_analysis"], dict):
        return row

    impact: dict[str, Any] = {}
    trade: dict[str, Any] = {}
    news_summary: dict[str, Any] = {}
    duration: dict[str, Any] = {}

    for key, val in list(row.items()):
        if key.startswith("impact_analysis."):
            impact[key.split(".", 1)[1]] = val
        elif key.startswith("trade_advice."):
            trade[key.split(".", 1)[1]] = val
        elif key.startswith("news_summary."):
            news_summary[key.split(".", 1)[1]] = val
        elif key.startswith("duration."):
            duration[key.split(".", 1)[1]] = val

    if impact:
        row["impact_analysis"] = impact
    if trade:
        row["trade_advice"] = trade
    if news_summary:
        row["news_summary"] = news_summary
    if duration:
        row["duration"] = duration
    return row


def _normalize_signal_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in records:
        row = _unnest_parquet_row(dict(raw))
        impact = row.get("impact_analysis") or {}
        trade = row.get("trade_advice") or {}
        duration = row.get("duration") or {}
        ts = _parse_signal_time(row)
        if ts is None and row.get("timestamp"):
            ts = pd.to_datetime(row["timestamp"], utc=True).to_pydatetime()
        if ts is None:
            continue

        # 已是 historical_signals 扁平格式
        if row.get("trend_level") and not impact:
            normalized.append(
                {
                    "news_id": str(row.get("news_id", "")),
                    "timestamp": ts,
                    "direction": row.get("direction", "neutral"),
                    "trend_level": row.get("trend_level", "L1"),
                    "composite_score": float(row.get("composite_score", 0) or 0),
                    "duration_hours": int(row.get("duration_hours", 12)),
                    "grid_spacing": float(row.get("grid_spacing", 2.0) or 2.0),
                    "spacing_factor": float(row.get("spacing_factor", 1.0) or 1.0),
                    "pause_reverse_orders": bool(row.get("pause_reverse_orders", False)),
                    "pause_all_new_orders": bool(row.get("pause_all_new_orders", False)),
                    "reverse_position_ratio": float(row.get("reverse_position_ratio", 1.0) or 1.0),
                    "max_positions": int(row.get("max_positions", 5) or 5),
                    "default_lot": float(row.get("default_lot", 0.01) or 0.01),
                    "title": str(row.get("title", "")),
                    "_source_file": row.get("_source", "historical"),
                }
            )
            continue

        normalized.append(
            {
                "news_id": str(row.get("news_id") or row.get("_record_id", "")),
                "timestamp": ts,
                "direction": impact.get("direction", "neutral"),
                "trend_level": impact.get("trend_level", "L1"),
                "composite_score": float(impact.get("composite_score", 0) or 0),
                "duration_hours": int(
                    impact.get("duration_hours")
                    or duration.get("duration_hours")
                    or 12
                ),
                "grid_spacing": float(trade.get("grid_spacing", 2.0) or 2.0),
                "spacing_factor": float(trade.get("spacing_factor", 1.0) or 1.0),
                "pause_reverse_orders": bool(trade.get("pause_reverse_orders", False)),
                "pause_all_new_orders": bool(trade.get("pause_all_new_orders", False)),
                "reverse_position_ratio": float(trade.get("reverse_position_ratio", 1.0) or 1.0),
                "max_positions": int(trade.get("max_positions", 5) or 5),
                "default_lot": float(trade.get("default_lot", 0.01) or 0.01),
                "title": (row.get("news_summary") or row.get("news") or {}).get("title", ""),
                "_source_file": row.get("_source_file", ""),
            }
        )
    normalized.sort(key=lambda x: x["timestamp"])
    return normalized


def load_price_bars(
    csv_path: Path,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    timeframe: str = "H1",
) -> pd.DataFrame:
    if not csv_path.is_file():
        raise FileNotFoundError(f"行情文件不存在: {csv_path}")

    df = pd.read_csv(
        csv_path,
        parse_dates=["datetime"],
        usecols=["datetime", "open", "high", "low", "close", "volume"],
    )
    if df["datetime"].dt.tz is None:
        df["datetime"] = df["datetime"].dt.tz_localize("UTC")
    else:
        df["datetime"] = df["datetime"].dt.tz_convert("UTC")

    if start is not None:
        start_utc = pd.Timestamp(start).tz_convert("UTC") if pd.Timestamp(start).tzinfo else pd.Timestamp(start, tz="UTC")
        df = df[df["datetime"] >= start_utc]
    if end is not None:
        end_utc = pd.Timestamp(end).tz_convert("UTC") if pd.Timestamp(end).tzinfo else pd.Timestamp(end, tz="UTC")
        df = df[df["datetime"] <= end_utc]

    df = df.sort_values("datetime").reset_index(drop=True)
    df["timeframe"] = timeframe
    return df


def load_m1_parquet(
    parquet_path: Path,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> pd.DataFrame:
    """加载 M1 Parquet（02 History Data/clean/XAUUSD_M1_clean.parquet）。"""
    if not parquet_path.is_file():
        raise FileNotFoundError(f"M1 行情不存在: {parquet_path}")

    df = pd.read_parquet(parquet_path, columns=["datetime", "open", "high", "low", "close", "volume"])
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)

    if start is not None:
        start_utc = pd.Timestamp(start).tz_convert("UTC") if pd.Timestamp(start).tzinfo else pd.Timestamp(start, tz="UTC")
        df = df[df["datetime"] >= start_utc]
    if end is not None:
        end_utc = pd.Timestamp(end).tz_convert("UTC") if pd.Timestamp(end).tzinfo else pd.Timestamp(end, tz="UTC")
        df = df[df["datetime"] <= end_utc]

    df = df.sort_values("datetime").reset_index(drop=True)
    df["timeframe"] = "M1"
    return df


def align_signals_to_price_window(
    signals: list[dict[str, Any]],
    price_start: datetime,
    price_end: datetime,
    *,
    non_overlapping: bool = True,
) -> list[dict[str, Any]]:
    """将信号映射到行情窗口。默认按槽位分配，避免全部重叠。"""
    if not signals:
        return []

    p_start = pd.Timestamp(price_start).tz_convert("UTC").to_pydatetime()
    p_end = pd.Timestamp(price_end).tz_convert("UTC").to_pydatetime()
    total_hours = (p_end - p_start).total_seconds() / 3600
    slot_hours = total_hours / len(signals) if signals else total_hours

    aligned: list[dict[str, Any]] = []
    for i, sig in enumerate(signals):
        if non_overlapping:
            new_ts = p_start + timedelta(hours=slot_hours * i)
            dur = min(int(sig.get("duration_hours", 12)), max(1, int(slot_hours)))
        else:
            sig_times = [s["timestamp"] for s in signals]
            t_min, t_max = min(sig_times), max(sig_times)
            span = (t_max - t_min).total_seconds() or 1.0
            ratio = (sig["timestamp"] - t_min).total_seconds() / span
            new_ts = p_start + timedelta(seconds=ratio * (p_end - p_start).total_seconds())
            dur = int(sig.get("duration_hours", 12))

        aligned.append(
            {
                **sig,
                "timestamp": new_ts,
                "duration_hours": dur,
                "_aligned": True,
                "_original_timestamp": sig["timestamp"],
            }
        )
    return aligned
