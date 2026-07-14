"""回测输出目录命名规范。"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

CHINA_TZ = timezone(timedelta(hours=8))

# 格式：YYYYMMDDHHmm，例 202607111958 → 2026年07月11日 19:58
RUN_ID_FORMAT = "%Y%m%d%H%M"


def output_filename(run_id: str, base_name: str) -> str:
    """生成带时间前缀的文件名：{YYYYMMDDHHmm}_{base_name}"""
    return f"{run_id}_{base_name}"


def make_run_id(when: datetime | None = None) -> str:
    """生成回测 run_id（北京时间，精确到分钟）。"""
    ts = when or datetime.now(CHINA_TZ)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=CHINA_TZ)
    else:
        ts = ts.astimezone(CHINA_TZ)
    return ts.strftime(RUN_ID_FORMAT)


def parse_run_id(run_id: str) -> datetime | None:
    """解析 run_id 为北京时间 datetime。"""
    try:
        return datetime.strptime(run_id, RUN_ID_FORMAT).replace(tzinfo=CHINA_TZ)
    except ValueError:
        return None


def allocate_run_id(base: Path, when: datetime | None = None) -> str:
    """分配不冲突的 run_id；同分钟重复则追加 _02。"""
    base.mkdir(parents=True, exist_ok=True)
    run_id = make_run_id(when)
    if not (base / output_filename(run_id, "comparison.json")).exists():
        return run_id
    for i in range(2, 100):
        candidate = f"{run_id}_{i:02d}"
        if not (base / output_filename(candidate, "comparison.json")).exists():
            return candidate
    raise RuntimeError(f"无法分配回测 run_id，请稍后重试: {base / run_id}")


def allocate_run_dir(base: Path, when: datetime | None = None) -> tuple[str, Path]:
    """兼容旧接口：返回 (run_id, 扁平输出目录 base)。"""
    run_id = allocate_run_id(base, when)
    return run_id, base
