"""宏观发布值 enrichment：日历 parquet / 本地 CSV / FRED 缓存 → actual/forecast/previous。"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# category → FRED series id（月度/周度观测）
FRED_SERIES: dict[str, str] = {
    "nfp": "PAYEMS",
    "cpi": "CPIAUCSL",
    "pce": "PCEPI",
    "gdp": "GDPC1",
    "retail": "RSAFS",
    "claims": "ICSA",
}

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"

# pct = 环比百分比；diff = 原序列一阶差分（NFP 就业人数变化等）
FRED_VALUE_MODE: dict[str, str] = {
    "cpi": "pct",
    "pce": "pct",
    "retail": "pct",
    "gdp": "pct",
    "nfp": "diff",
    "claims": "diff",
}

MONTHLY_CATEGORIES = frozenset({"nfp", "cpi", "pce", "gdp", "retail", "ism"})
WEEKLY_CATEGORIES = frozenset({"claims"})


def _norm_title(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().strip())


def _title_overlap(a: str, b: str) -> bool:
    ta, tb = _norm_title(a), _norm_title(b)
    if not ta or not tb:
        return False
    keys = (
        "cpi", "pce", "payroll", "nfp", "nonfarm", "gdp", "retail",
        "jobless", "claims", "fomc", "ism",
    )
    for k in keys:
        if k in ta and k in tb:
            return True
    return ta[:12] in tb or tb[:12] in ta


def merge_calendar_actuals(events: pd.DataFrame, calendar: pd.DataFrame) -> pd.DataFrame:
    """将 economic_calendar 中的 actual/forecast/previous 合并到事件表。"""
    if events.empty or calendar.empty:
        return events
    out = events.copy()
    for col in ("actual", "forecast", "previous"):
        if col not in out.columns:
            out[col] = None

    cal = calendar.copy()
    if "datetime_utc" in cal.columns:
        cal["_dt"] = pd.to_datetime(cal["datetime_utc"], utc=True)
    elif "datetime" in cal.columns:
        cal["_dt"] = pd.to_datetime(cal["datetime"], utc=True)
    else:
        return out

    events_dt = pd.to_datetime(out["datetime"], utc=True)
    merged = 0
    for i, ev_dt in enumerate(events_dt):
        if pd.notna(out.at[out.index[i], "actual"]):
            continue
        day = ev_dt.normalize()
        title = str(out.at[out.index[i], "event_name"])
        day_match = cal["_dt"].dt.normalize() == day
        time_match = (cal["_dt"] - ev_dt).abs() <= pd.Timedelta(hours=3)
        cands = cal[day_match | time_match]
        for _, crow in cands.iterrows():
            if _title_overlap(title, str(crow.get("event_name", ""))):
                for col in ("actual", "forecast", "previous"):
                    val = crow.get(col)
                    if val is not None and str(val).strip() not in ("", "nan"):
                        out.at[out.index[i], col] = val
                merged += 1
                break
    if merged:
        logger.info("从 economic_calendar 合并 actual/forecast: %d 条", merged)
    return out


def load_macro_releases_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame()
    df = pd.read_csv(path)
    need = {"date", "category"}
    if not need.issubset(set(c.lower() for c in df.columns)):
        return pd.DataFrame()
    colmap = {c.lower(): c for c in df.columns}
    out = pd.DataFrame({
        "date": pd.to_datetime(df[colmap["date"]]).dt.date,
        "category": df[colmap["category"]].astype(str).str.lower(),
    })
    for col in ("actual", "forecast", "previous"):
        if col in colmap:
            out[col] = df[colmap[col]]
    return out.dropna(subset=["date"])


def apply_macro_releases_csv(events: pd.DataFrame, releases: pd.DataFrame) -> pd.DataFrame:
    if events.empty or releases.empty:
        return events
    out = events.copy()
    for col in ("actual", "forecast", "previous"):
        if col not in out.columns:
            out[col] = None
    merged = 0
    for i, row in out.iterrows():
        if pd.notna(row.get("actual")):
            continue
        ev_dt = pd.to_datetime(row["datetime"], utc=True).date()
        cat = str(row.get("category", "") or "").lower()
        if not cat:
            t = _norm_title(str(row.get("event_name", "")))
            for c in FRED_SERIES:
                if c in t or (c == "nfp" and "payroll" in t):
                    cat = c
                    break
        m = releases[(releases["date"] == ev_dt) & (releases["category"] == cat)]
        if m.empty and cat:
            m = releases[(releases["date"] == ev_dt)]
        if m.empty:
            continue
        rec = m.iloc[0]
        for col in ("actual", "forecast", "previous"):
            if col in rec and pd.notna(rec[col]):
                out.at[i, col] = rec[col]
        merged += 1
    if merged:
        logger.info("从 macro_releases.csv 合并: %d 条", merged)
    return out


def _fred_release_frame(obs: pd.DataFrame, category: str) -> pd.DataFrame:
    """按品类生成 actual / forecast / previous（forecast 用上期环比作朴素预期）。"""
    mode = FRED_VALUE_MODE.get(category, "pct")
    df = obs.sort_values("observation_date").reset_index(drop=True).copy()
    if mode == "pct":
        df["actual"] = df["value"].pct_change() * 100.0
        df["forecast"] = df["actual"].shift(1)
        df["previous"] = df["value"].pct_change().shift(1) * 100.0
    else:
        df["actual"] = df["value"].diff()
        df["forecast"] = df["actual"].shift(1)
        df["previous"] = df["value"].shift(1)
    return df.dropna(subset=["actual", "forecast"])


def fetch_fred_series(series_id: str, *, timeout: int = 45) -> pd.DataFrame:
    import requests

    url = FRED_CSV_URL.format(series_id=series_id)
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text))
    if df.shape[1] < 2:
        return pd.DataFrame()
    df.columns = ["observation_date", "value"]
    df["observation_date"] = pd.to_datetime(df["observation_date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df.dropna(subset=["value"]).sort_values("observation_date")


def build_fred_release_cache(history_root: Path, *, force: bool = False) -> pd.DataFrame:
    """拉取 FRED 序列并缓存；网络失败时返回已有缓存。"""
    cache_dir = history_root / "macro"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "fred_releases.parquet"
    if cache_path.is_file() and not force:
        try:
            return pd.read_parquet(cache_path)
        except Exception:
            pass

    rows: list[dict[str, Any]] = []
    for category, series_id in FRED_SERIES.items():
        try:
            obs = fetch_fred_series(series_id)
        except Exception as exc:
            logger.warning("FRED %s 拉取失败: %s", series_id, exc)
            continue
        if obs.empty:
            continue
        rel = _fred_release_frame(obs, category)
        for _, r in rel.iterrows():
            rows.append({
                "observation_date": r["observation_date"].date(),
                "category": category,
                "actual": r["actual"],
                "forecast": r["forecast"],
                "previous": r["previous"],
                "series_id": series_id,
            })
    if not rows:
        if cache_path.is_file():
            return pd.read_parquet(cache_path)
        return pd.DataFrame()
    cache = pd.DataFrame(rows)
    cache.to_parquet(cache_path, index=False)
    logger.info("FRED 缓存已写入 %s (%d 行)", cache_path, len(cache))
    return cache


def apply_fred_cache(events: pd.DataFrame, cache: pd.DataFrame) -> pd.DataFrame:
    """按 category + 日期邻近匹配 FRED 发布值。"""
    if events.empty or cache.empty:
        return events
    out = events.copy()
    for col in ("actual", "forecast", "previous"):
        if col not in out.columns:
            out[col] = None
    merged = 0
    cache = cache.copy()
    cache["observation_date"] = pd.to_datetime(cache["observation_date"]).dt.date

    for i, row in out.iterrows():
        if pd.notna(row.get("actual")):
            continue
        cat = str(row.get("category", "") or "").lower()
        if cat not in FRED_SERIES:
            t = _norm_title(str(row.get("event_name", "")))
            for c, keys in (
                ("nfp", ("payroll", "nfp", "nonfarm")),
                ("cpi", ("cpi",)),
                ("pce", ("pce",)),
                ("gdp", ("gdp",)),
                ("retail", ("retail",)),
                ("claims", ("jobless", "claims")),
            ):
                if any(k in t for k in keys):
                    cat = c
                    break
        if cat not in FRED_SERIES:
            continue
        ev_date = pd.to_datetime(row["datetime"], utc=True)
        ev_d = ev_date.date()
        sub = cache[cache["category"] == cat].copy()
        if sub.empty:
            continue
        if cat in MONTHLY_CATEGORIES:
            ev_period = ev_date.to_period("M")
            sub["period"] = pd.to_datetime(sub["observation_date"]).dt.to_period("M")
            sub = sub[sub["period"] == ev_period]
            if sub.empty:
                continue
            best = sub.iloc[0]
        elif cat in WEEKLY_CATEGORIES:
            sub["dist"] = sub["observation_date"].apply(
                lambda d: abs((pd.Timestamp(d) - pd.Timestamp(ev_d)).days)
            )
            best = sub.loc[sub["dist"].idxmin()]
            if best["dist"] > 7:
                continue
        else:
            sub["dist"] = sub["observation_date"].apply(
                lambda d: abs((pd.Timestamp(d) - pd.Timestamp(ev_d)).days)
            )
            best = sub.loc[sub["dist"].idxmin()]
            if best["dist"] > 21:
                continue
        out.at[i, "actual"] = best["actual"]
        out.at[i, "forecast"] = best["forecast"]
        out.at[i, "previous"] = best["previous"]
        merged += 1
    if merged:
        logger.info("从 FRED 缓存合并 actual/forecast: %d 条", merged)
    return out


def enrich_events_with_actuals(events: pd.DataFrame, history_root: Path) -> pd.DataFrame:
    """日历 parquet → 本地 CSV → FRED 缓存，依次 enrichment。"""
    if events.empty:
        return events
    for col in ("actual", "forecast", "previous"):
        if col not in events.columns:
            events[col] = None

    cal_path = history_root / "calendar" / "economic_calendar.parquet"
    if cal_path.is_file():
        try:
            cal = pd.read_parquet(cal_path)
            events = merge_calendar_actuals(events, cal)
        except Exception as exc:
            logger.warning("读取 economic_calendar 失败: %s", exc)

    csv_path = history_root / "macro" / "macro_releases.csv"
    releases = load_macro_releases_csv(csv_path)
    if not releases.empty:
        events = apply_macro_releases_csv(events, releases)

    try:
        cache = build_fred_release_cache(history_root, force=False)
        if not cache.empty:
            events = apply_fred_cache(events, cache)
    except Exception as exc:
        logger.warning("FRED enrichment 跳过: %s", exc)

    has_actual = events["actual"].notna().sum() if "actual" in events.columns else 0
    logger.info("enrichment 后含 actual 的事件: %d / %d", has_actual, len(events))
    return events
