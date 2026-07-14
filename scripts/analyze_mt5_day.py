"""分析指定交易日的 MT5 成交 + 系统决策，输出胜率摘要。"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import yaml  # noqa: E402

CHINA_TZ = timezone(timedelta(hours=8))
ENTRY_IN = 0
ENTRY_OUT = 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", default=datetime.now(CHINA_TZ).strftime("%Y-%m-%d"))
    args = parser.parse_args()
    day = args.day

    cfg = yaml.safe_load((PROJECT_ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    mt5c = cfg["mt5"]
    magic = int(mt5c.get("magic", 20260711))

    import MetaTrader5 as mt5

    if not mt5.initialize(path=mt5c.get("path") or None):
        print("MT5 initialize failed", mt5.last_error())
        return 1
    info = mt5.account_info()
    if info is None or info.login != mt5c["account"]:
        if not mt5.login(mt5c["account"], password=mt5c["password"], server=mt5c["server"]):
            print("MT5 login failed", mt5.last_error())
            mt5.shutdown()
            return 1
        info = mt5.account_info()

    day0 = datetime.fromisoformat(f"{day}T00:00:00").replace(tzinfo=CHINA_TZ)
    day1 = day0 + timedelta(days=1)
    from_dt = day0.astimezone(timezone.utc).replace(tzinfo=None)
    to_dt = day1.astimezone(timezone.utc).replace(tzinfo=None)
    deals = mt5.history_deals_get(from_dt, to_dt) or ()
    positions = mt5.positions_get(symbol=mt5c["symbol"]) or ()
    orders = mt5.orders_get(symbol=mt5c["symbol"]) or ()

    rows = []
    for d in deals:
        rows.append(
            {
                "time": datetime.fromtimestamp(d.time, tz=timezone.utc)
                .astimezone(CHINA_TZ)
                .isoformat(),
                "ticket": d.ticket,
                "order": d.order,
                "type": int(d.type),
                "entry": int(d.entry),
                "volume": float(d.volume),
                "price": float(d.price),
                "profit": float(d.profit),
                "commission": float(d.commission),
                "swap": float(d.swap),
                "magic": int(d.magic),
                "comment": d.comment or "",
                "symbol": d.symbol,
                "position_id": int(d.position_id),
            }
        )

    our = [
        r
        for r in rows
        if r["magic"] == magic or "gold-news" in r["comment"]
    ]
    if not our:
        our = [r for r in rows if r["symbol"] in ("GOLD", "XAUUSD")]

    opens = [r for r in our if r["entry"] == ENTRY_IN]
    closes = [r for r in our if r["entry"] == ENTRY_OUT]
    # balance ops entry=2 sometimes; ignore balance deals

    by_pos: dict[int, list] = defaultdict(list)
    for r in our:
        if r["position_id"]:
            by_pos[r["position_id"]].append(r)

    round_trips = []
    for pid, events in by_pos.items():
        events = sorted(events, key=lambda x: x["time"])
        in_e = [e for e in events if e["entry"] == ENTRY_IN]
        out_e = [e for e in events if e["entry"] == ENTRY_OUT]
        if not out_e:
            continue
        pnl = sum(e["profit"] + e["commission"] + e["swap"] for e in out_e)
        # also commission on open
        pnl += sum(e["commission"] + e["swap"] for e in in_e)
        side = "buy" if (in_e and in_e[0]["type"] == 0) else "sell"
        round_trips.append(
            {
                "position_id": pid,
                "side": side,
                "open_time": in_e[0]["time"] if in_e else "",
                "close_time": out_e[-1]["time"],
                "open_price": in_e[0]["price"] if in_e else None,
                "close_price": out_e[-1]["price"],
                "volume": sum(e["volume"] for e in out_e),
                "pnl": round(pnl, 2),
                "comment": (in_e[0]["comment"] if in_e else out_e[0]["comment"]),
                "win": pnl > 0,
            }
        )

    wins = [t for t in round_trips if t["win"]]
    losses = [t for t in round_trips if not t["win"] and t["pnl"] != 0]
    flats = [t for t in round_trips if t["pnl"] == 0]
    decided = wins + losses
    hit_rate = (len(wins) / len(decided)) if decided else None
    total_pnl = round(sum(t["pnl"] for t in round_trips), 2)
    gross_win = round(sum(t["pnl"] for t in wins), 2)
    gross_loss = round(sum(t["pnl"] for t in losses), 2)
    pf = (gross_win / abs(gross_loss)) if gross_loss else None

    # decisions
    runtime = Path(cfg.get("runtime", {}).get("dir", "data/runtime"))
    jsonl = PROJECT_ROOT / runtime / f"decisions_{day}.jsonl"
    decisions = []
    if jsonl.is_file():
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                decisions.append(json.loads(line))

    exec_rows = [d for d in decisions if d.get("mt5_executed")]
    dirs = Counter(d.get("direction_cn") for d in decisions)
    levels = Counter(d.get("trend_level") for d in decisions)
    exec_actions = Counter()
    for d in exec_rows:
        for a in d.get("mt5_actions") or []:
            if a.startswith("place_buy"):
                exec_actions["place_buy_limit"] += 1
            elif a.startswith("place_sell"):
                exec_actions["place_sell_limit"] += 1
            elif a.startswith("close_reverse"):
                exec_actions["close_reverse"] += 1
            elif a.startswith("take_profit"):
                exec_actions["take_profit"] += 1
            elif a.startswith("cancel"):
                exec_actions["cancel"] += 1
            elif a.startswith("set_tp") or a.startswith("set_pending_tp"):
                exec_actions["set_tp"] += 1

    # equity path from decisions
    equity_pts = []
    for d in decisions:
        bid = d.get("bid")
        # no equity in decision log always — skip
    # use market snapshots from signals if needed

    result = {
        "day": day,
        "generated_at": datetime.now(CHINA_TZ).isoformat(),
        "account": {
            "login": info.login,
            "balance": float(info.balance),
            "equity": float(info.equity),
            "profit": float(info.profit),
            "server": info.server,
        },
        "mt5": {
            "deals_total": len(rows),
            "deals_ours": len(our),
            "opens": len(opens),
            "closes": len(closes),
            "open_positions": len(positions),
            "pending_orders": len(orders),
            "round_trips": len(round_trips),
            "wins": len(wins),
            "losses": len(losses),
            "flats": len(flats),
            "hit_rate": round(hit_rate, 4) if hit_rate is not None else None,
            "hit_rate_pct": round(hit_rate * 100, 2) if hit_rate is not None else None,
            "total_pnl": total_pnl,
            "gross_win": gross_win,
            "gross_loss": gross_loss,
            "profit_factor": round(pf, 3) if pf is not None else None,
            "buy_opens": sum(1 for r in opens if r["type"] == 0),
            "sell_opens": sum(1 for r in opens if r["type"] == 1),
        },
        "system": {
            "decisions": len(decisions),
            "mt5_executed_signals": len(exec_rows),
            "directions": dict(dirs),
            "levels": dict(levels),
            "exec_action_counts": dict(exec_actions),
        },
        "round_trips": round_trips,
        "deals": our,
        "positions": [
            {
                "ticket": p.ticket,
                "type": "buy" if p.type == 0 else "sell",
                "volume": float(p.volume),
                "price_open": float(p.price_open),
                "price_current": float(p.price_current),
                "profit": float(p.profit),
                "magic": int(p.magic),
                "comment": p.comment,
                "tp": float(p.tp),
            }
            for p in positions
        ],
        "orders": [
            {
                "ticket": o.ticket,
                "type": int(o.type),
                "volume": float(o.volume_current),
                "price_open": float(o.price_open),
                "magic": int(o.magic),
                "comment": o.comment,
                "tp": float(o.tp),
            }
            for o in orders
        ],
    }

    out_dir = PROJECT_ROOT / "docs" / "操作记录" / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / f"mt5_day_analysis_{day.replace('-', '')}.json"
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    md = []
    md.append(f"# MT5 日交易分析 {day}")
    md.append("")
    md.append(f"> 生成：{result['generated_at']}")
    md.append("")
    md.append("## 账户")
    md.append(
        f"- 余额 **{info.balance:.2f}** · 净值 **{info.equity:.2f}** · 浮动盈亏 **{info.profit:.2f}**"
    )
    md.append("")
    md.append("## 成交与胜率（已平仓 round-trip）")
    m = result["mt5"]
    md.append(f"- 开仓成交：{m['opens']}（买 {m['buy_opens']} / 卖 {m['sell_opens']}）")
    md.append(f"- 平仓成交：{m['closes']}")
    md.append(f"- 已完成回合：{m['round_trips']}（胜 {m['wins']} / 负 {m['losses']} / 平 {m['flats']}）")
    if m["hit_rate_pct"] is not None:
        md.append(f"- **胜率 {m['hit_rate_pct']}%**（{m['wins']}/{len(decided)}）")
    else:
        md.append("- 胜率：暂无已平仓回合可统计")
    md.append(f"- 已实现盈亏合计：**{m['total_pnl']:+.2f} USD**")
    if m["profit_factor"] is not None:
        md.append(f"- Profit Factor：**{m['profit_factor']}**")
    md.append(f"- 当前持仓 {m['open_positions']} · 挂单 {m['pending_orders']}")
    md.append("")
    md.append("## 系统决策侧")
    s = result["system"]
    md.append(f"- 决策条数：{s['decisions']} · 执行过 MT5 的信号：{s['mt5_executed_signals']}")
    md.append(f"- 方向：{s['directions']}")
    md.append(f"- 等级：{s['levels']}")
    md.append(f"- 动作统计：{s['exec_action_counts']}")
    md.append("")
    if round_trips:
        md.append("## 已平仓明细")
        md.append("")
        md.append("| 开仓 | 平仓 | 方向 | 量 | 开/平价 | 盈亏 |")
        md.append("|------|------|------|----|---------|------|")
        for t in round_trips:
            md.append(
                f"| {t['open_time'][11:16]} | {t['close_time'][11:16]} | {t['side']} | "
                f"{t['volume']:.2f} | {t['open_price']}/{t['close_price']} | {t['pnl']:+.2f} |"
            )
        md.append("")
    out_md = out_dir / f"mt5_day_analysis_{day.replace('-', '')}.md"
    out_md.write_text("\n".join(md), encoding="utf-8")

    print(json.dumps({k: result[k] for k in ("day", "account", "mt5", "system")}, ensure_ascii=False, indent=2))
    print(f"已写入: {out_md}")
    print(f"已写入: {out_json}")

    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
