"""MT5 环境诊断：检查终端配置与 Python 连接前置条件。

不修改 MT5 配置文件，不输出密码。
"""

from __future__ import annotations

import configparser
import json
import os
import sys
from pathlib import Path


def _default_mt5_data_dir() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    root = Path(appdata) / "MetaQuotes" / "Terminal"
    if not root.is_dir():
        return None
    candidates = sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    for item in candidates:
        if (item / "config" / "common.ini").is_file():
            return item
    return None


def _read_common_ini(data_dir: Path) -> dict[str, str]:
    ini_path = data_dir / "config" / "common.ini"
    parser = configparser.ConfigParser()
    parser.read(ini_path, encoding="utf-16")
    common = parser["Common"] if parser.has_section("Common") else {}
    experts = parser["Experts"] if parser.has_section("Experts") else {}
    return {
        "login": common.get("Login", ""),
        "server": common.get("Server", ""),
        "allow_dll_import": experts.get("AllowDllImport", ""),
        "experts_enabled": experts.get("Enabled", ""),
        "experts_api": experts.get("Api", ""),
        "ini_path": str(ini_path),
    }


def _scan_recent_log(data_dir: Path, account: str, expected_server: str) -> dict[str, object]:
    logs_dir = data_dir / "logs"
    if not logs_dir.is_dir():
        return {"log_found": False}

    log_files = sorted(logs_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not log_files:
        return {"log_found": False}

    latest = log_files[0]
    text = latest.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()[-400:]
    auth_fail = [ln for ln in lines if "authorization" in ln.lower() and account in ln]
    wrong_server = [ln for ln in lines if expected_server.lower() not in ln.lower() and account in ln and "authorization" in ln.lower()]
    algo_lines = [ln for ln in lines if "automated trading" in ln.lower()][-6:]
    fxpro_hits = [ln for ln in lines if "fxpro" in ln.lower()][-3:]

    return {
        "log_found": True,
        "log_file": str(latest),
        "recent_auth_failures": auth_fail[:5],
        "recent_algo_toggle": algo_lines,
        "recent_fxpro_mentions": fxpro_hits,
        "likely_wrong_server": bool(auth_fail) and not any(expected_server.lower() in ln.lower() for ln in auth_fail),
    }


def _probe_python_mt5() -> dict[str, object]:
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return {"package_installed": False}

    rc = mt5.initialize()
    err = mt5.last_error()
    info = mt5.account_info()
    mt5.shutdown()
    return {
        "package_installed": True,
        "initialize_ok": rc,
        "last_error": str(err),
        "terminal_login": info.login if info else None,
        "terminal_server": info.server if info else None,
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")

    project_root = Path(__file__).resolve().parents[1]
    config_path = project_root / "config" / "config.yaml"
    expected = {"account": "", "server": "FxPro-MT5 Demo", "path": ""}
    if config_path.is_file():
        import yaml

        with config_path.open(encoding="utf-8") as f:
            mt5_cfg = yaml.safe_load(f).get("mt5", {})
        expected["account"] = str(mt5_cfg.get("account", ""))
        expected["server"] = str(mt5_cfg.get("server", "FxPro-MT5 Demo"))
        expected["path"] = str(mt5_cfg.get("path", ""))

    data_dir = _default_mt5_data_dir()
    report: dict[str, object] = {
        "project_config": expected,
        "mt5_data_dir": str(data_dir) if data_dir else None,
        "issues": [],
        "recommendations": [],
    }

    if not data_dir:
        report["issues"].append("未找到 MT5 数据目录（%APPDATA%\\MetaQuotes\\Terminal\\*）")
    else:
        ini = _read_common_ini(data_dir)
        report["terminal_common_ini"] = ini

        if expected["account"] and ini["login"] == expected["account"] and ini["server"] != expected["server"]:
            report["issues"].append(
                f"终端缓存服务器错误：common.ini 中 Server={ini['server']}，"
                f"但项目期望 {expected['server']}"
            )
            report["recommendations"].append(
                "在 MT5 中：文件 → 开立账户 → 搜索 FxPro → 选择 FxPro-MT5 Demo → "
                "用现有账户登录；或安装 FxPro 官方 MT5 客户端"
            )

        if ini["experts_enabled"] == "0":
            report["issues"].append("common.ini 中 [Experts] Enabled=0（算法交易默认关闭）")
            report["recommendations"].append(
                "关闭 MT5 后，在 工具→选项→EA交易 勾选「允许算法交易」，"
                "并确认 common.ini 中 Enabled=1、Api=1"
            )

        if ini["experts_api"] == "0":
            report["issues"].append("common.ini 中 [Experts] Api=0（Python API 算法交易被禁用）")
            report["recommendations"].append(
                "在 MT5：工具→选项→EA交易，勾选允许通过 Python/API 进行算法交易（如有该选项）"
            )

        if ini["allow_dll_import"] != "1":
            report["issues"].append("common.ini 中 AllowDllImport 未启用")

        report["log_scan"] = _scan_recent_log(data_dir, expected["account"], expected["server"])

    report["python_probe"] = _probe_python_mt5()
    probe = report["python_probe"]
    if isinstance(probe, dict) and probe.get("package_installed"):
        if not probe.get("initialize_ok"):
            report["issues"].append(f"Python 无法附着 MT5：{probe.get('last_error')}")
        elif probe.get("terminal_server") and probe["terminal_server"] != expected["server"]:
            report["issues"].append(
                f"终端当前服务器 {probe['terminal_server']} 与 config 中 {expected['server']} 不一致"
            )

    if not report["issues"]:
        report["status"] = "ok"
    else:
        report["status"] = "needs_attention"

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
