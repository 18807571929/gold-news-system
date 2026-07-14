"""信号桥接：读取分析结果 → 生成交易建议 → 推送 MT5。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import yaml

from src.mt5_bridge.connector import MT5Connector
from src.mt5_bridge.grid_executor import GridExecutor
from src.risk_manager.risk_checker import RiskManager
from src.runtime.decision_log import DecisionLogger
from src.strategy_adapter.atr_provider import ATRProvider
from src.strategy_adapter.grid_adapter import GridAdapter

logger = logging.getLogger(__name__)
CHINA_TZ = timezone(timedelta(hours=8))


class SignalBridge:
    """连接情感分析结果与 MT5 交易执行。"""

    def __init__(
        self,
        sentiment_cache_dir: str | Path = "data/sentiment_cache",
        signal_output_dir: str | Path = "data/signals",
        connector: MT5Connector | None = None,
        grid_adapter: GridAdapter | None = None,
        atr_provider: ATRProvider | None = None,
        grid_executor: GridExecutor | None = None,
        risk_manager: RiskManager | None = None,
        enabled: bool = False,
        dry_run: bool = True,
        auto_execute: bool = False,
        runtime_dir: str | Path = "data/runtime",
    ) -> None:
        self.sentiment_cache_dir = Path(sentiment_cache_dir)
        self.signal_output_dir = Path(signal_output_dir)
        self.connector = connector or MT5Connector.from_config()
        self.grid_adapter = grid_adapter or GridAdapter.from_config()
        self.atr_provider = atr_provider or ATRProvider.from_config()
        self.grid_executor = grid_executor or GridExecutor.from_config(self.connector)
        self.risk_manager = risk_manager or RiskManager.from_config()
        self.enabled = enabled
        self.dry_run = dry_run
        self.auto_execute = auto_execute
        self.decision_log = DecisionLogger(runtime_dir)

        self.signal_output_dir.mkdir(parents=True, exist_ok=True)
        self._executed_path = self.signal_output_dir / "executed_ids.json"
        self._executed_ids: set[str] = self._load_executed_ids()

    @classmethod
    def from_config(cls, config_path: str = "config/config.yaml") -> SignalBridge:
        with Path(config_path).open(encoding="utf-8") as f:
            config = yaml.safe_load(f)

        mt5_cfg = config.get("mt5", {})
        nlp_cfg = config.get("nlp", {})
        runtime_cfg = config.get("runtime", {})
        return cls(
            sentiment_cache_dir=nlp_cfg.get("output_dir", "data/sentiment_cache"),
            signal_output_dir=mt5_cfg.get("signal_dir", "data/signals"),
            enabled=bool(mt5_cfg.get("enabled", False)),
            dry_run=bool(mt5_cfg.get("dry_run", True)),
            auto_execute=bool(mt5_cfg.get("auto_execute", False)),
            runtime_dir=runtime_cfg.get("dir", "data/runtime"),
        )

    def _load_executed_ids(self) -> set[str]:
        if not self._executed_path.exists():
            return set()
        try:
            with self._executed_path.open(encoding="utf-8") as f:
                data = json.load(f)
            return set(data) if isinstance(data, list) else set()
        except (json.JSONDecodeError, OSError):
            return set()

    def _persist_executed_ids(self) -> None:
        with self._executed_path.open("w", encoding="utf-8") as f:
            json.dump(sorted(self._executed_ids), f, ensure_ascii=False, indent=2)

    def _build_signal(
        self,
        sentiment_data: dict[str, Any],
        atr_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        atr_info = atr_info or {}
        advice = self.grid_adapter.adapt(
            sentiment_data,
            atr_factor=float(atr_info.get("atr_factor", 1.0)),
        )
        news = sentiment_data.get("news", {})

        return {
            "timestamp": datetime.now(CHINA_TZ).isoformat(),
            "news_id": sentiment_data.get("news_id", ""),
            "news_summary": {
                "title": news.get("title", ""),
                "time": news.get("time", ""),
                "source": news.get("source", ""),
            },
            "rule_adjustment": sentiment_data.get("rule_adjustment"),
            "market_context": {
                "atr": atr_info.get("atr"),
                "atr_factor": atr_info.get("atr_factor"),
                "atr_source": atr_info.get("atr_source"),
                "atr_period": atr_info.get("atr_period"),
            },
            **advice.to_dict(),
        }

    def _apply_to_mt5(
        self,
        signal: dict[str, Any],
        account_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """根据建议调整 MT5 挂单（删反向、减仓、布网格）。"""
        return self.grid_executor.execute(
            signal,
            dry_run=self.dry_run,
            account_info=account_info,
        )

    def process_once(self, *, execute: bool | None = None) -> list[dict[str, Any]]:
        """处理所有未推送的情感分析结果，生成并可选执行交易信号。"""
        do_execute = self.auto_execute if execute is None else execute
        if do_execute and not self.enabled:
            logger.warning("execute=true 但 mt5.enabled=false，跳过执行")
            do_execute = False

        results: list[dict[str, Any]] = []
        atr_info = self.atr_provider.get_atr(self.connector if self.enabled else None)

        for json_file in sorted(self.sentiment_cache_dir.glob("*/*.json")):
            if json_file.name in ("processed_ids.json",):
                continue

            with json_file.open(encoding="utf-8") as f:
                sentiment_data = json.load(f)

            if "trend_score" not in sentiment_data:
                continue

            news_id = sentiment_data.get("news_id", "")
            if news_id in self._executed_ids:
                continue

            signal = self._build_signal(sentiment_data, atr_info)

            account_dict = None
            positions_count = 0
            if self.connector.is_available and not self.dry_run:
                if self.connector.connect():
                    acct = self.connector.get_account_info()
                    if acct:
                        account_dict = {
                            "balance": acct.balance,
                            "equity": acct.equity,
                            "margin": acct.margin,
                            "peak_equity": max(acct.balance, acct.equity),
                        }
                        signal["account_snapshot"] = {
                            "login": acct.login,
                            "equity": acct.equity,
                            "balance": acct.balance,
                            "margin_free": acct.margin_free,
                        }
                    positions_count = len(self.connector.get_positions())
                    tick = self.connector.get_tick()
                    if tick and tick.bid > 0:
                        signal["market_snapshot"] = {
                            "symbol": tick.symbol,
                            "bid": tick.bid,
                            "ask": tick.ask,
                        }
                    self.connector.disconnect()

            risk = self.risk_manager.check(signal, account_dict, positions_count)
            signal["risk_check"] = risk.to_dict()

            if not risk.passed:
                signal["execution"] = {"executed": False, "blocked": risk.blocked_reason}
                logger.warning("风控拦截 [%s]: %s", news_id, risk.blocked_reason)
            elif do_execute:
                signal["execution"] = self._apply_to_mt5(signal, account_dict)
            else:
                signal["execution"] = {
                    "executed": False,
                    "reason": "execute=false（新闻引擎仅写 JSON，不执行 MT5）",
                }

            date_part = datetime.now(CHINA_TZ).strftime("%Y-%m-%d")
            out_dir = self.signal_output_dir / date_part
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{news_id}.json"
            with out_path.open("w", encoding="utf-8") as f:
                json.dump(signal, f, ensure_ascii=False, indent=2)

            self.decision_log.append(
                DecisionLogger.from_signal(signal, signal_path=str(out_path))
            )

            self._executed_ids.add(news_id)
            results.append(signal)

            logger.info(
                "信号生成 [%s] %s %s → %s",
                news_id,
                signal["impact_analysis"]["direction_cn"],
                signal["impact_analysis"]["trend_level"],
                signal["trade_advice"]["action_cn"],
            )

        if results:
            self._persist_executed_ids()
        return results

    def execute_pending_signals(self, day: str | None = None) -> list[dict[str, Any]]:
        """扫描已生成信号，对 execution.executed=false 且风控通过的条目执行 MT5。"""
        if not self.enabled or not self.auto_execute:
            logger.info("MT5 未启用或 auto_execute=false，跳过 pending 执行")
            return []

        day = day or datetime.now(CHINA_TZ).strftime("%Y-%m-%d")
        signal_dir = self.signal_output_dir / day
        if not signal_dir.is_dir():
            return []

        executed: list[dict[str, Any]] = []
        for path in sorted(signal_dir.glob("*.json")):
            with path.open(encoding="utf-8") as f:
                signal = json.load(f)
            ex = signal.get("execution") or {}
            if ex.get("executed"):
                continue
            risk = signal.get("risk_check") or {}
            if risk and not risk.get("passed", True):
                continue

            account_dict = None
            if self.connector.connect():
                acct = self.connector.get_account_info()
                if acct:
                    account_dict = {
                        "balance": acct.balance,
                        "equity": acct.equity,
                        "margin": acct.margin,
                        "peak_equity": max(acct.balance, acct.equity),
                    }
                self.connector.disconnect()

            signal["execution"] = self._apply_to_mt5(signal, account_dict)
            with path.open("w", encoding="utf-8") as f:
                json.dump(signal, f, ensure_ascii=False, indent=2)
            self.decision_log.append(
                DecisionLogger.from_signal(signal, signal_path=str(path))
            )
            executed.append(signal)
            logger.info("补执行 [%s] actions=%s", signal.get("news_id"), signal["execution"].get("actions_taken"))
        return executed


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    bridge = SignalBridge.from_config()
    signals = bridge.process_once()
    print(f"生成 {len(signals)} 条交易信号")


if __name__ == "__main__":
    main()
