"""历史宏观事件 → 回测用新闻冲击信号（1~3 年）。"""

from __future__ import annotations

import json
import logging
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.backtest_adapter.loader import load_price_bars
from src.backtest_adapter.macro_actuals import enrich_events_with_actuals
from src.trend_scorer.price_context import momentum_at_time
from src.strategy_adapter.grid_adapter import (
    LEVEL_ACTIONS,
    PriceShockConfig,
    apply_price_shock_adjustment,
)

logger = logging.getLogger(__name__)

UTC = timezone.utc

# 事件关键词 → (fine_category, default_level, duration_hours)
EVENT_RULES: list[tuple[tuple[str, ...], str, str, int]] = [
    (("nonfarm", "nfp", "payrolls"), "非农数据", "L3", 24),
    (("cpi", "consumer price"), "通胀数据", "L3", 24),
    (("pce", "core pce"), "通胀数据", "L2", 12),
    (("fomc", "fed funds", "interest rate decision", "federal reserve"), "利率决策", "L3", 48),
    (("powell", "fed chair"), "央行政策", "L2", 24),
    (("gdp",), "宏观数据", "L2", 12),
    (("retail sales",), "宏观数据", "L2", 12),
    (("ism manufacturing", "ism services"), "宏观数据", "L2", 12),
    (("jobless claims",), "宏观数据", "L1", 6),
    (("geopolitical", "war", "conflict", "sanction"), "地缘冲突", "L3", 72),
]

# 实际值高于预期 → 利空黄金（美元偏强 / 紧缩预期）
HAWKISH_IF_HIGHER_CATEGORIES = frozenset({"nfp", "cpi", "pce", "gdp", "retail", "ism"})
# 实际值高于预期 → 利多黄金（经济偏弱）
DOVISH_IF_HIGHER_CATEGORIES = frozenset({"claims"})
DEFAULT_L3_SHOCK_HOURS = 4
DEFAULT_EXCLUDE_CATEGORIES = frozenset({"claims"})
MOMENTUM_DIRECTION_THRESHOLD = 12.0
DEFAULT_MACRO_SCORE = {"L1": 45.0, "L2": 55.0, "L3": 70.0, "L4": 85.0}


def _first_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    count = 0
    while d.month == month:
        if d.weekday() == weekday:
            count += 1
            if count == n:
                return d
        d += timedelta(days=1)
    return date(year, month, monthrange(year, month)[1])


# FOMC 公布日（约，UTC 18:00）；来源：美联储公开日程摘要
FOMC_DATES = [
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26", "2023-09-20",
    "2023-11-01", "2023-12-13",
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18",
    "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17",
    "2025-11-05", "2025-12-17",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16",
    "2026-11-04", "2026-12-16",
]


def generate_macro_schedule(start: date, end: date) -> pd.DataFrame:
    """生成美国宏观高影响事件时间表（NFP/CPI/PCE/GDP/零售/ISM/失业金/FOMC）。"""
    rows: list[dict[str, Any]] = []

    y, m = start.year, start.month
    while date(y, m, 1) <= end:
        nfp = _first_friday(y, m)
        if start <= nfp <= end:
            rows.append({
                "event_date": nfp,
                "event_time_utc": "12:30:00",
                "event_name": "Nonfarm Payrolls",
                "category": "nfp",
                "importance": 3,
            })
        cpi_day = min(13, monthrange(y, m)[1])
        cpi = date(y, m, cpi_day)
        if start <= cpi <= end:
            rows.append({
                "event_date": cpi,
                "event_time_utc": "12:30:00",
                "event_name": "US CPI YoY",
                "category": "cpi",
                "importance": 3,
            })
        pce_day = min(28, monthrange(y, m)[1])
        pce = date(y, m, pce_day)
        if start <= pce <= end:
            rows.append({
                "event_date": pce,
                "event_time_utc": "12:30:00",
                "event_name": "Core PCE Price Index",
                "category": "pce",
                "importance": 3,
            })
        retail_day = min(15, monthrange(y, m)[1])
        retail = date(y, m, retail_day)
        if start <= retail <= end:
            rows.append({
                "event_date": retail,
                "event_time_utc": "12:30:00",
                "event_name": "Retail Sales",
                "category": "retail",
                "importance": 3,
            })
        ism_mfg = _nth_weekday(y, m, 0, 1)  # 第一个周一
        if start <= ism_mfg <= end:
            rows.append({
                "event_date": ism_mfg,
                "event_time_utc": "14:00:00",
                "event_name": "ISM Manufacturing PMI",
                "category": "ism",
                "importance": 3,
            })
        ism_svc = _nth_weekday(y, m, 0, 1) + timedelta(days=2)
        if start <= ism_svc <= end:
            rows.append({
                "event_date": ism_svc,
                "event_time_utc": "14:00:00",
                "event_name": "ISM Services PMI",
                "category": "ism",
                "importance": 3,
            })
        if m in (1, 4, 7, 10):
            gdp_day = min(25, monthrange(y, m)[1])
            gdp = date(y, m, gdp_day)
            if start <= gdp <= end:
                rows.append({
                    "event_date": gdp,
                    "event_time_utc": "12:30:00",
                    "event_name": "GDP QoQ",
                    "category": "gdp",
                    "importance": 3,
                })
        m += 1
        if m > 12:
            m = 1
            y += 1

    # 每周四初请失业金
    d = start
    while d <= end:
        if d.weekday() == 3:
            rows.append({
                "event_date": d,
                "event_time_utc": "12:30:00",
                "event_name": "Initial Jobless Claims",
                "category": "claims",
                "importance": 3,
            })
        d += timedelta(days=1)

    for ds in FOMC_DATES:
        d = date.fromisoformat(ds)
        if start <= d <= end:
            rows.append({
                "event_date": d,
                "event_time_utc": "18:00:00",
                "event_name": "FOMC Statement",
                "category": "fomc",
                "importance": 3,
            })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["datetime"] = pd.to_datetime(
        df["event_date"].astype(str) + " " + df["event_time_utc"],
        utc=True,
    )
    return df.sort_values("datetime").reset_index(drop=True)


def merge_event_schedules(*frames: pd.DataFrame) -> pd.DataFrame:
    """合并多源事件表并去重。"""
    parts: list[pd.DataFrame] = []
    for df in frames:
        if df is None or df.empty:
            continue
        sub = df.copy()
        if "datetime" not in sub.columns and "datetime_utc" in sub.columns:
            sub["datetime"] = pd.to_datetime(sub["datetime_utc"], utc=True)
        if "event_name" not in sub.columns and "title" in sub.columns:
            sub["event_name"] = sub["title"]
        parts.append(sub)
    if not parts:
        return pd.DataFrame()
    merged = pd.concat(parts, ignore_index=True)
    merged["datetime"] = pd.to_datetime(merged["datetime"], utc=True)
    merged = merged.drop_duplicates(subset=["datetime", "event_name"], keep="first")
    return merged.sort_values("datetime").reset_index(drop=True)


def load_calendar_parquet(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    if "datetime_utc" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime_utc"], utc=True)
    elif "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    else:
        return pd.DataFrame()
    if "event_name" not in df.columns and "title" in df.columns:
        df["event_name"] = df["title"]
    return df


def _classify_event(title: str) -> tuple[str, str, int]:
    t = title.lower()
    for keys, fine, level, hours in EVENT_RULES:
        if any(k in t for k in keys):
            return fine, level, hours
    return "其他", "L2", 12


def filter_events(
    events: pd.DataFrame,
    *,
    exclude_categories: set[str] | frozenset[str] | None = None,
) -> pd.DataFrame:
    """剔除低价值/噪声事件（如每周初请失业金）。"""
    if events.empty:
        return events
    exclude = {c.lower() for c in (exclude_categories or DEFAULT_EXCLUDE_CATEGORIES)}
    if not exclude:
        return events

    def _should_drop(row: pd.Series) -> bool:
        cat = str(row.get("category", "") or "").lower()
        if cat in exclude:
            return True
        title = str(row.get("event_name", "")).lower()
        if "claims" in exclude and ("jobless" in title or "claims" in title):
            return True
        return False

    mask = ~events.apply(_should_drop, axis=1)
    dropped = int((~mask).sum())
    if dropped:
        logger.info("过滤事件类别 %s: 剔除 %d 条", sorted(exclude), dropped)
    return events.loc[mask].reset_index(drop=True)


def _event_category(row: pd.Series, title: str) -> str:
    cat = str(row.get("category", "") or "").strip().lower()
    if cat:
        return cat
    t = title.lower()
    if any(k in t for k in ("nonfarm", "payroll", "nfp")):
        return "nfp"
    if "cpi" in t or "consumer price" in t:
        return "cpi"
    if "pce" in t:
        return "pce"
    if "fomc" in t or "fed funds" in t:
        return "fomc"
    if "gdp" in t:
        return "gdp"
    if "retail" in t:
        return "retail"
    if "ism" in t:
        return "ism"
    if "jobless" in t or "claims" in t:
        return "claims"
    if "powell" in t or "fed chair" in t:
        return "fed_speech"
    return "macro"


def _parse_econ_number(val: Any) -> float | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip().replace(",", "")
    if not s or s.lower() in ("n/a", "nan", "-", ""):
        return None
    mult = 1.0
    if s.endswith("%"):
        s = s[:-1]
    elif s.upper().endswith("K"):
        mult = 1000.0
        s = s[:-1]
    elif s.upper().endswith("M"):
        mult = 1_000_000.0
        s = s[:-1]
    elif s.upper().endswith("B"):
        mult = 1_000_000_000.0
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


def _surprise_is_significant(diff: float, ref: float, category: str = "") -> bool:
    if abs(diff) < 1e-9:
        return False
    cat = (category or "").lower()
    # 品类阈值：pct 类用更小绝对值；NFP/claims 用更大绝对值
    min_abs: dict[str, float] = {
        "cpi": 0.05,
        "pce": 0.05,
        "gdp": 0.10,
        "retail": 0.15,
        "nfp": 20.0,
        "claims": 5000.0,
    }
    floor = min_abs.get(cat, 0.05)
    if abs(ref) > 1e-9:
        rel_thr = 0.03 if cat in ("cpi", "pce") else 0.05
        return abs(diff / ref) >= rel_thr or abs(diff) >= floor
    return abs(diff) >= floor


def _direction_from_surprise(
    diff: float,
    *,
    category: str,
    title: str,
) -> str:
    """diff = actual - reference；按黄金常见宏观逻辑映射方向。"""
    cat = category.lower()
    title_l = title.lower()
    hawkish = (
        cat in HAWKISH_IF_HIGHER_CATEGORIES
        or any(k in title_l for k in ("cpi", "pce", "payroll", "nfp", "gdp", "retail", "ism"))
    )
    dovish_if_higher = cat in DOVISH_IF_HIGHER_CATEGORIES or "jobless" in title_l or "claims" in title_l
    fomc = cat == "fomc" or "fomc" in title_l or "fed funds" in title_l

    if fomc:
        if diff > 0:
            return "bearish"
        if diff < 0:
            return "bullish"
        return "neutral"
    if hawkish and not dovish_if_higher:
        if diff > 0:
            return "bearish"
        if diff < 0:
            return "bullish"
        return "neutral"
    if dovish_if_higher:
        if diff > 0:
            return "bullish"
        if diff < 0:
            return "bearish"
        return "neutral"
    return "neutral"


def _surprise_direction(
    row: pd.Series,
    title: str,
    category: str,
) -> tuple[str, float, str]:
    actual = _parse_econ_number(row.get("actual"))
    forecast = _parse_econ_number(row.get("forecast"))
    previous = _parse_econ_number(row.get("previous"))
    ref = forecast if forecast is not None else previous
    if actual is None or ref is None:
        return "neutral", 0.0, ""
    diff = actual - ref
    if not _surprise_is_significant(diff, ref, category):
        return "neutral", 0.0, ""
    direction = _direction_from_surprise(diff, category=category, title=title)
    if direction == "neutral":
        return "neutral", 0.0, ""
    rel = abs(diff / ref) if abs(ref) > 1e-9 else abs(diff)
    score = min(100.0, 55.0 + rel * 120.0)
    return direction, score, "surprise"


def _momentum_direction(momentum: float, *, threshold: float = MOMENTUM_DIRECTION_THRESHOLD) -> str:
    if momentum >= threshold:
        return "bullish"
    if momentum <= -threshold:
        return "bearish"
    return "neutral"


def _resolve_event_direction(
    row: pd.Series,
    title: str,
    *,
    dict_direction: str,
    dict_score: float,
    momentum: float,
    level: str,
    category: str,
    surprise_direction_exclude: frozenset[str] | set[str] | None = None,
    momentum_direction_enabled: bool = True,
    momentum_threshold: float = MOMENTUM_DIRECTION_THRESHOLD,
) -> tuple[str, float, str]:
    """宏观方向：词典 → 实际/预期意外 → H1 动量（可配置关闭）。"""
    if dict_direction in ("bullish", "bearish"):
        mag = abs(dict_score) if dict_score else DEFAULT_MACRO_SCORE.get(level, 55.0)
        return dict_direction, mag, "dict"

    exclude = {c.lower() for c in (surprise_direction_exclude or ())}
    surprise_dir, surprise_score, surprise_src = _surprise_direction(row, title, category)
    if category.lower() in exclude:
        if surprise_score > 0:
            return "neutral", surprise_score, "surprise_neutral"
    elif surprise_dir in ("bullish", "bearish"):
        return surprise_dir, surprise_score, surprise_src

    if momentum_direction_enabled:
        mom_dir = _momentum_direction(momentum, threshold=momentum_threshold)
        if mom_dir != "neutral":
            mom_score = min(100.0, max(momentum_threshold, abs(momentum)))
            return mom_dir, mom_score, "momentum"

    return "neutral", 0.0, ""


def _dict_sentiment(title: str, dict_path: Path) -> tuple[str, float]:
    try:
        from src.nlp_engine.sentiment_analyzer import SentimentAnalyzer

        analyzer = SentimentAnalyzer(dict_path=str(dict_path), mode="dict")
        result = analyzer.analyze({"title": title, "source": "宏观日历", "summary": title})
        return result.direction, float(result.score)
    except Exception:
        return "neutral", 0.0


def events_to_signals(
    events: pd.DataFrame,
    *,
    base_spacing: float = 2.0,
    default_lot: float = 0.01,
    max_layers: int = 5,
    dict_path: Path | None = None,
    bars: pd.DataFrame | None = None,
    price_blend: float = 0.15,
    price_shock_cfg: PriceShockConfig | None = None,
    l3_shock_hours: int = DEFAULT_L3_SHOCK_HOURS,
    category_level_overrides: dict[str, str] | None = None,
    surprise_direction_exclude: frozenset[str] | set[str] | None = None,
    momentum_direction_enabled: bool = True,
    momentum_direction_threshold: float = MOMENTUM_DIRECTION_THRESHOLD,
) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    dict_path = dict_path or Path("data/sentiment_dict/gold_sentiment.yaml")
    use_price = bars is not None and not bars.empty and price_blend > 0

    for _, row in events.iterrows():
        title = str(row.get("event_name", "Macro Event"))
        ts = row["datetime"]
        if isinstance(ts, pd.Timestamp):
            ts = ts.to_pydatetime()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)

        fine, level, duration_h = _classify_event(title)
        category = _event_category(row, title)
        if category_level_overrides and category.lower() in category_level_overrides:
            level = str(category_level_overrides[category.lower()]).upper()
        dict_direction, dict_score = _dict_sentiment(title, dict_path)
        cfg = LEVEL_ACTIONS.get(level, LEVEL_ACTIONS["L2"])

        momentum = 0.0
        if bars is not None and not bars.empty:
            ts_pd = pd.Timestamp(ts).tz_convert("UTC") if ts.tzinfo else pd.Timestamp(ts, tz=UTC)
            momentum = momentum_at_time(bars, ts_pd)

        direction, base_mag, dir_source = _resolve_event_direction(
            row,
            title,
            dict_direction=dict_direction,
            dict_score=dict_score,
            momentum=momentum,
            level=level,
            category=category,
            surprise_direction_exclude=set(surprise_direction_exclude or ()),
            momentum_direction_enabled=momentum_direction_enabled,
            momentum_threshold=momentum_direction_threshold,
        )
        news_mag = base_mag if base_mag > 0 else DEFAULT_MACRO_SCORE.get(level, 55.0)
        composite_score = news_mag

        if use_price and price_blend > 0:
            mom_mag = abs(momentum)
            composite_score = news_mag * (1 - price_blend) + mom_mag * price_blend
            if dir_source == "surprise":
                # surprise 方向不因动量冲突被抹平，仅轻微调分
                if direction == "bullish" and momentum < -20:
                    composite_score *= 0.85
                elif direction == "bearish" and momentum > 20:
                    composite_score *= 0.85
                elif direction == "bullish" and momentum > 20:
                    composite_score = min(100.0, composite_score * 1.1)
                elif direction == "bearish" and momentum < -20:
                    composite_score = min(100.0, composite_score * 1.1)
            else:
                if direction == "bullish" and momentum < -20:
                    direction = "neutral"
                    composite_score *= 0.6
                elif direction == "bearish" and momentum > 20:
                    direction = "neutral"
                    composite_score *= 0.6
                elif direction == "bullish" and momentum > 20:
                    composite_score = min(100.0, composite_score * 1.1)
                elif direction == "bearish" and momentum < -20:
                    composite_score = min(100.0, composite_score * 1.1)

        cfg, shock_note = apply_price_shock_adjustment(
            cfg, direction, momentum, price_shock_cfg,
        )

        news_id = f"hist_{ts.strftime('%Y%m%d%H%M')}_{category or 'macro'}"
        spacing = base_spacing * cfg["spacing_factor"]
        max_pos = max(1, int(max_layers * cfg["max_positions_factor"]))
        shock_hours = l3_shock_hours if level == "L3" else duration_h

        signals.append({
            "news_id": news_id,
            "timestamp": ts,
            "direction": direction,
            "trend_level": level,
            "composite_score": round(composite_score, 2),
            "price_momentum": round(momentum, 2),
            "duration_hours": duration_h,
            "shock_duration_hours": shock_hours,
            "grid_spacing": spacing,
            "spacing_factor": cfg["spacing_factor"],
            "pause_reverse_orders": cfg["pause_reverse"],
            "pause_all_new_orders": cfg["pause_all_new"],
            "reverse_position_ratio": cfg["reverse_ratio"],
            "max_positions": max_pos,
            "default_lot": default_lot,
            "title": title,
            "_source": "historical_macro",
            "_fine_category": fine,
            "_direction_source": dir_source,
            "_price_shock_note": shock_note,
        })
    return signals


def build_historical_signals(
    years: float = 3.0,
    *,
    end: date | None = None,
    history_root: Path | None = None,
    dict_path: Path | None = None,
    base_spacing: float = 2.0,
    default_lot: float = 0.01,
    max_layers: int = 5,
    price_blend: float = 0.20,
    price_shock_cfg: PriceShockConfig | None = None,
    exclude_categories: set[str] | frozenset[str] | None = None,
    l3_shock_hours: int = DEFAULT_L3_SHOCK_HOURS,
    force_generate: bool = False,
    category_level_overrides: dict[str, str] | None = None,
    surprise_direction_exclude: frozenset[str] | set[str] | None = None,
    momentum_direction_enabled: bool = True,
    momentum_direction_threshold: float = MOMENTUM_DIRECTION_THRESHOLD,
) -> list[dict[str, Any]]:
    end = end or date.today()
    start = end - timedelta(days=int(years * 365.25))
    history_root = history_root or Path("E:/量化项目/02 History Data")

    cal_path = history_root / "calendar" / "economic_calendar.parquet"
    ns_path = history_root / "calendar" / "news_schedule.parquet"

    events = load_calendar_parquet(ns_path)
    if events.empty:
        events = load_calendar_parquet(cal_path)

    generated = generate_macro_schedule(start, end)
    min_events = int(years * 24)

    if force_generate:
        events = generated
    elif events.empty:
        logger.warning("经济日历为空，使用扩展内置宏观日程")
        events = generated
    else:
        span_days = (events["datetime"].max() - events["datetime"].min()).days
        if len(events) < min_events or span_days < int(years * 300):
            logger.warning(
                "经济日历仅 %d 条 / 跨度 %d 天，与内置日程合并",
                len(events),
                span_days,
            )
        events = merge_event_schedules(events, generated)
        events = events[
            (events["datetime"] >= pd.Timestamp(start, tz=UTC))
            & (events["datetime"] <= pd.Timestamp(end, tz=UTC))
        ]

    events = enrich_events_with_actuals(events, history_root)
    events = filter_events(events, exclude_categories=exclude_categories)

    logger.info("历史信号事件数: %d", len(events))

    bars: pd.DataFrame | None = None
    h1_csv = history_root / "data" / "XAUUSD_H1.csv"
    if h1_csv.is_file():
        bars = load_price_bars(h1_csv)
        logger.info("已加载 H1 行情 %d 根，用于价格上下文", len(bars))
    else:
        logger.warning("H1 行情不存在: %s，跳过价格上下文", h1_csv)

    return events_to_signals(
        events,
        base_spacing=base_spacing,
        default_lot=default_lot,
        max_layers=max_layers,
        dict_path=dict_path,
        bars=bars,
        price_blend=price_blend,
        price_shock_cfg=price_shock_cfg,
        l3_shock_hours=l3_shock_hours,
        category_level_overrides=category_level_overrides,
        surprise_direction_exclude=set(surprise_direction_exclude or ()),
        momentum_direction_enabled=momentum_direction_enabled,
        momentum_direction_threshold=momentum_direction_threshold,
    )


def save_historical_signals(
    signals: list[dict[str, Any]],
    out_path: Path,
) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    records = []
    for s in signals:
        rec = {**s, "timestamp": pd.Timestamp(s["timestamp"]).isoformat()}
        records.append(rec)
    df = pd.json_normalize(records)
    df.to_parquet(out_path, index=False)
    meta = {
        "count": len(signals),
        "start": records[0]["timestamp"] if records else None,
        "end": records[-1]["timestamp"] if records else None,
        "path": str(out_path),
    }
    out_path.with_suffix(".meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return len(signals)


def load_historical_signals_parquet(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    df = pd.read_parquet(path)
    records = df.to_dict(orient="records")
    out: list[dict[str, Any]] = []
    for r in records:
        ts = pd.to_datetime(r.get("timestamp"), utc=True).to_pydatetime()
        out.append({**r, "timestamp": ts})
    return out
