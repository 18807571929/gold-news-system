"""MT5 平台连接封装。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import yaml

logger = logging.getLogger(__name__)

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None  # type: ignore


@dataclass
class AccountInfo:
    login: int
    balance: float
    equity: float
    margin: float
    margin_free: float
    profit: float
    currency: str


@dataclass
class SymbolTick:
    symbol: str
    bid: float
    ask: float
    time: int


class MT5Connector:
    """MetaTrader5 连接管理器。"""

    def __init__(
        self,
        account: int = 0,
        password: str = "",
        server: str = "",
        symbol: str = "XAUUSD",
        terminal_path: str = "",
    ) -> None:
        self.account = account
        self.password = password
        self.server = server
        self.symbol = symbol
        self.terminal_path = terminal_path
        self._connected = False

    @classmethod
    def from_config(cls, config_path: str = "config/config.yaml") -> MT5Connector:
        from pathlib import Path

        with Path(config_path).open(encoding="utf-8") as f:
            config = yaml.safe_load(f)

        mt5_cfg = config.get("mt5", {})
        return cls(
            account=int(mt5_cfg.get("account", 0)),
            password=str(mt5_cfg.get("password", "")),
            server=str(mt5_cfg.get("server", "")),
            symbol=str(mt5_cfg.get("symbol", "XAUUSD")),
            terminal_path=str(mt5_cfg.get("path", "")),
        )

    @property
    def is_available(self) -> bool:
        return mt5 is not None

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _initialize_terminal(self) -> bool:
        """按优先级尝试附着/启动 MT5 终端。"""
        if mt5.initialize():
            return True

        if self.terminal_path and mt5.initialize(path=self.terminal_path):
            return True

        if self.terminal_path and self.account and self.password and self.server:
            logger.info(
                "尝试用账号凭证启动 MT5 account=%s server=%s",
                self.account,
                self.server,
            )
            if mt5.initialize(
                path=self.terminal_path,
                login=self.account,
                password=self.password,
                server=self.server,
            ):
                return True

        return False

    def _servers_match(self, actual: str, expected: str) -> bool:
        if not actual or not expected:
            return True
        return actual.strip().lower() == expected.strip().lower()

    def connect(self) -> bool:
        if not self.is_available:
            logger.error("MetaTrader5 包未安装，请 pip install MetaTrader5")
            return False

        if not self._initialize_terminal():
            err = mt5.last_error()
            logger.error("MT5 初始化失败: %s", err)
            logger.error(
                "请确认：1) MT5 终端已打开且右下角已登录 FxPro 账号 "
                "2) 工具→选项→EA交易→勾选「允许算法交易」和「允许 DLL 导入」 "
                "3) common.ini 中 Server 必须是 FxPro-MT5 Demo（不是 MetaQuotes-Demo） "
                "4) 以普通用户运行 Python（不要管理员与 MT5 权限不一致）"
            )
            return False

        info = mt5.account_info()
        if info is None:
            logger.error("MT5 终端未登录交易账号，请先在 MT5 界面完成登录")
            mt5.shutdown()
            return False

        if self.server and not self._servers_match(info.server, self.server):
            logger.error(
                "终端当前服务器 %s 与 config 中 %s 不一致。"
                "请在 MT5 中：文件→开立账户→搜索 FxPro→选择 FxPro-MT5 Demo 登录",
                info.server,
                self.server,
            )

        if self.account and info.login != self.account:
            logger.warning(
                "终端当前账号 %s 与 config 中 %s 不一致，将使用终端当前账号",
                info.login,
                self.account,
            )

        if self.password and self.server:
            if info.login == self.account or not self.account:
                if self._servers_match(info.server, self.server):
                    logger.info("MT5 已登录目标服务器 account=%s server=%s", info.login, info.server)
                else:
                    authorized = mt5.login(
                        login=self.account or info.login,
                        password=self.password,
                        server=self.server,
                    )
                    if not authorized:
                        err = mt5.last_error()
                        logger.error("MT5 API 登录失败: %s", err)
                        logger.error(
                            "账号/密码/服务器不匹配，或 MT5 未添加 FxPro 服务器。"
                            "建议：文件→开立账户→搜索 FxPro；或在 MT5 手动登录后将 config 中 password 留空 \"\""
                        )
                        if mt5.account_info() and self._servers_match(mt5.account_info().server, self.server):
                            logger.warning("继续使用 MT5 终端当前已登录的会话")
                        else:
                            mt5.shutdown()
                            return False
                    else:
                        logger.info("MT5 API 登录成功 account=%s server=%s", self.account, self.server)
            else:
                logger.info(
                    "跳过 API 重新登录，使用终端当前账号 account=%s server=%s",
                    info.login,
                    info.server,
                )
        else:
            logger.info("MT5 使用终端当前会话 account=%s server=%s", info.login, info.server)

        info = mt5.account_info()
        if info and self.server and not self._servers_match(info.server, self.server):
            logger.error("连接后服务器仍为 %s，期望 %s", info.server, self.server)
            mt5.shutdown()
            return False

        self._resolve_symbol(self.symbol)

        self._connected = True
        return True

    GOLD_SYMBOL_ALIASES = ("XAUUSD", "GOLD", "XAUUSDm", "XAUUSD.")

    def _resolve_symbol(self, preferred: str | None = None) -> str | None:
        """选择可用黄金品种（FxPro 为 GOLD，其他券商常为 XAUUSD）。"""
        if not self.is_available:
            return None
        candidates: list[str] = []
        for sym in (preferred, self.symbol, *self.GOLD_SYMBOL_ALIASES):
            if sym and sym not in candidates:
                candidates.append(sym)
        for sym in candidates:
            if mt5.symbol_select(sym, True):
                tick = mt5.symbol_info_tick(sym)
                if tick and tick.bid > 0:
                    if sym != self.symbol:
                        logger.info("品种 %s 不可用，已切换为 %s", self.symbol, sym)
                    self.symbol = sym
                    return sym
        return None

    def ensure_symbol(self, symbol: str | None = None) -> bool:
        if not self._connected or not self.is_available:
            return False
        if self._resolve_symbol(symbol):
            return True
        logger.warning("无法选择品种 %s（已尝试 %s）", symbol or self.symbol, self.GOLD_SYMBOL_ALIASES)
        return False

    def check_connection(self) -> dict[str, Any]:
        """测试连接并返回账户/行情快照。"""
        result: dict[str, Any] = {"connected": False}
        if not self.connect():
            result["error"] = "连接失败"
            if self.is_available:
                result["last_error"] = str(mt5.last_error())
            return result

        try:
            acct = self.get_account_info()
            if acct:
                info = mt5.account_info()
                result["account"] = {
                    "login": acct.login,
                    "server": info.server if info else (self.server or "current"),
                    "balance": acct.balance,
                    "equity": acct.equity,
                    "margin_free": acct.margin_free,
                    "currency": acct.currency,
                }
            self.ensure_symbol()
            tick = self.get_tick()
            if tick and tick.bid > 0:
                result["tick"] = {"symbol": tick.symbol, "bid": tick.bid, "ask": tick.ask}
            result["positions"] = len(self.get_positions())
            result["pending_orders"] = len(self.get_pending_orders())
            result["connected"] = True
        finally:
            self.disconnect()
        return result

    def disconnect(self) -> None:
        if self.is_available and self._connected:
            mt5.shutdown()
        self._connected = False

    def get_account_info(self) -> AccountInfo | None:
        if not self._connected or not self.is_available:
            return None
        info = mt5.account_info()
        if info is None:
            return None
        return AccountInfo(
            login=info.login,
            balance=info.balance,
            equity=info.equity,
            margin=info.margin,
            margin_free=info.margin_free,
            profit=info.profit,
            currency=info.currency,
        )

    def get_tick(self, symbol: str | None = None) -> SymbolTick | None:
        if not self._connected or not self.is_available:
            return None
        sym = symbol or self.symbol
        self.ensure_symbol(sym)
        tick = mt5.symbol_info_tick(sym)
        if tick is None:
            return None
        return SymbolTick(symbol=sym, bid=tick.bid, ask=tick.ask, time=tick.time)

    def get_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if not self._connected or not self.is_available:
            return []
        sym = symbol or self.symbol
        positions = mt5.positions_get(symbol=sym)
        if positions is None:
            return []
        return [
            {
                "ticket": p.ticket,
                "symbol": p.symbol,
                "type": "buy" if p.type == 0 else "sell",
                "volume": p.volume,
                "price_open": p.price_open,
                "price_current": p.price_current,
                "profit": p.profit,
                "sl": p.sl,
                "tp": p.tp,
                "magic": p.magic,
                "comment": p.comment,
            }
            for p in positions
        ]

    def get_pending_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if not self._connected or not self.is_available:
            return []
        sym = symbol or self.symbol
        orders = mt5.orders_get(symbol=sym)
        if orders is None:
            return []
        order_types = {0: "buy", 1: "sell", 2: "buy_limit", 3: "sell_limit", 4: "buy_stop", 5: "sell_stop"}
        return [
            {
                "ticket": o.ticket,
                "symbol": o.symbol,
                "type": order_types.get(o.type, str(o.type)),
                "volume": o.volume_current,
                "price_open": o.price_open,
                "sl": o.sl,
                "tp": o.tp,
                "magic": o.magic,
                "comment": o.comment,
            }
            for o in orders
        ]

    def delete_order(self, ticket: int) -> bool:
        if not self._connected or not self.is_available:
            return False
        request = {"action": mt5.TRADE_ACTION_REMOVE, "order": ticket}
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.warning("删除挂单 %s 失败: %s", ticket, mt5.last_error())
            return False
        return True

    def get_symbol_specs(self, symbol: str | None = None) -> dict[str, Any] | None:
        """品种精度、最小手数、止损距离等。"""
        if not self._connected or not self.is_available:
            return None
        sym = symbol or self.symbol
        self.ensure_symbol(sym)
        info = mt5.symbol_info(sym)
        if info is None:
            return None
        return {
            "symbol": sym,
            "digits": info.digits,
            "point": info.point,
            "volume_min": info.volume_min,
            "volume_max": info.volume_max,
            "volume_step": info.volume_step,
            "trade_stops_level": info.trade_stops_level,
            "filling_mode": info.filling_mode,
        }

    def _pick_filling_mode(self, symbol: str) -> int:
        info = mt5.symbol_info(symbol)
        if info is None:
            return mt5.ORDER_FILLING_RETURN
        mode = info.filling_mode
        ioc = getattr(mt5, "SYMBOL_FILLING_IOC", 2)
        fok = getattr(mt5, "SYMBOL_FILLING_FOK", 1)
        if mode & ioc:
            return mt5.ORDER_FILLING_IOC
        if mode & fok:
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    def normalize_price(self, price: float, symbol: str | None = None) -> float:
        sym = symbol or self.symbol
        specs = self.get_symbol_specs(sym)
        digits = specs["digits"] if specs else 2
        return round(price, digits)

    def normalize_volume(self, volume: float, symbol: str | None = None) -> float:
        sym = symbol or self.symbol
        specs = self.get_symbol_specs(sym)
        if not specs:
            return volume
        step = specs["volume_step"] or 0.01
        vol_min = specs["volume_min"] or step
        vol_max = specs["volume_max"] or volume
        steps = round(volume / step)
        normalized = max(vol_min, min(vol_max, steps * step))
        return round(normalized, 8)

    def place_pending_order(
        self,
        order_type: str,
        price: float,
        volume: float,
        *,
        symbol: str | None = None,
        magic: int = 0,
        comment: str = "",
        sl: float = 0.0,
        tp: float = 0.0,
    ) -> dict[str, Any]:
        """下限价/止损挂单。order_type: buy_limit | sell_limit | buy_stop | sell_stop"""
        if not self._connected or not self.is_available:
            return {"ok": False, "error": "not_connected"}

        sym = symbol or self.symbol
        self.ensure_symbol(sym)
        specs = self.get_symbol_specs(sym)
        if not specs:
            return {"ok": False, "error": "symbol_info_unavailable"}

        type_map = {
            "buy_limit": mt5.ORDER_TYPE_BUY_LIMIT,
            "sell_limit": mt5.ORDER_TYPE_SELL_LIMIT,
            "buy_stop": mt5.ORDER_TYPE_BUY_STOP,
            "sell_stop": mt5.ORDER_TYPE_SELL_STOP,
        }
        mt5_type = type_map.get(order_type)
        if mt5_type is None:
            return {"ok": False, "error": f"invalid_order_type:{order_type}"}

        norm_price = self.normalize_price(price, sym)
        norm_volume = self.normalize_volume(volume, sym)
        if norm_volume <= 0:
            return {"ok": False, "error": "invalid_volume"}

        tick = self.get_tick(sym)
        if tick:
            min_dist = specs["trade_stops_level"] * specs["point"]
            if order_type == "buy_limit" and norm_price >= tick.ask - min_dist:
                return {"ok": False, "error": "buy_limit_too_close_to_market"}
            if order_type == "sell_limit" and norm_price <= tick.bid + min_dist:
                return {"ok": False, "error": "sell_limit_too_close_to_market"}

        request: dict[str, Any] = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": sym,
            "volume": norm_volume,
            "type": mt5_type,
            "price": norm_price,
            "deviation": 20,
            "magic": magic,
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._pick_filling_mode(sym),
        }
        if sl > 0:
            request["sl"] = self.normalize_price(sl, sym)
        if tp > 0:
            request["tp"] = self.normalize_price(tp, sym)

        result = mt5.order_send(request)
        if result is None:
            return {"ok": False, "error": str(mt5.last_error())}
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return {
                "ok": False,
                "error": f"retcode_{result.retcode}",
                "comment": result.comment,
            }
        return {
            "ok": True,
            "ticket": result.order,
            "price": norm_price,
            "volume": norm_volume,
            "type": order_type,
        }

    def close_position(
        self,
        ticket: int,
        volume: float | None = None,
        *,
        symbol: str | None = None,
        magic: int = 0,
        comment: str = "",
    ) -> dict[str, Any]:
        """平仓（可部分平仓）。"""
        if not self._connected or not self.is_available:
            return {"ok": False, "error": "not_connected"}

        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return {"ok": False, "error": "position_not_found"}

        pos = positions[0]
        sym = symbol or pos.symbol
        close_vol = volume if volume is not None else pos.volume
        close_vol = self.normalize_volume(close_vol, sym)
        if close_vol <= 0 or close_vol > pos.volume:
            close_vol = pos.volume

        tick = self.get_tick(sym)
        if not tick:
            return {"ok": False, "error": "no_tick"}

        if pos.type == mt5.POSITION_TYPE_BUY:
            order_type = mt5.ORDER_TYPE_SELL
            price = tick.bid
        else:
            order_type = mt5.ORDER_TYPE_BUY
            price = tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": sym,
            "volume": close_vol,
            "type": order_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": magic,
            "comment": comment[:31],
            "type_filling": self._pick_filling_mode(sym),
        }
        result = mt5.order_send(request)
        if result is None:
            return {"ok": False, "error": str(mt5.last_error())}
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return {"ok": False, "error": f"retcode_{result.retcode}", "comment": result.comment}
        return {"ok": True, "ticket": ticket, "volume": close_vol, "deal": result.deal}

    def modify_position_sltp(
        self,
        ticket: int,
        *,
        sl: float = 0.0,
        tp: float = 0.0,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        """修改持仓止损/止盈。"""
        if not self._connected or not self.is_available:
            return {"ok": False, "error": "not_connected"}

        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            return {"ok": False, "error": "position_not_found"}

        pos = positions[0]
        sym = symbol or pos.symbol
        request: dict[str, Any] = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": sym,
            "position": ticket,
            "sl": self.normalize_price(sl, sym) if sl > 0 else float(pos.sl or 0.0),
            "tp": self.normalize_price(tp, sym) if tp > 0 else float(pos.tp or 0.0),
        }
        result = mt5.order_send(request)
        if result is None:
            return {"ok": False, "error": str(mt5.last_error())}
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return {"ok": False, "error": f"retcode_{result.retcode}", "comment": result.comment}
        return {"ok": True, "ticket": ticket, "sl": request["sl"], "tp": request["tp"]}

    def modify_order_sltp(
        self,
        ticket: int,
        *,
        sl: float = 0.0,
        tp: float = 0.0,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        """修改挂单止损/止盈。"""
        if not self._connected or not self.is_available:
            return {"ok": False, "error": "not_connected"}

        orders = mt5.orders_get(ticket=ticket)
        if not orders:
            return {"ok": False, "error": "order_not_found"}

        order = orders[0]
        sym = symbol or order.symbol
        request: dict[str, Any] = {
            "action": mt5.TRADE_ACTION_MODIFY,
            "order": ticket,
            "symbol": sym,
            "price": float(order.price_open),
            "sl": self.normalize_price(sl, sym) if sl > 0 else float(order.sl or 0.0),
            "tp": self.normalize_price(tp, sym) if tp > 0 else float(order.tp or 0.0),
            "type_time": order.type_time,
            "type_filling": order.type_filling,
        }
        result = mt5.order_send(request)
        if result is None:
            return {"ok": False, "error": str(mt5.last_error())}
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return {"ok": False, "error": f"retcode_{result.retcode}", "comment": result.comment}
        return {"ok": True, "ticket": ticket, "sl": request["sl"], "tp": request["tp"]}

    def __enter__(self) -> MT5Connector:
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.disconnect()


def main() -> None:
    import json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    connector = MT5Connector.from_config()
    result = connector.check_connection()
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
