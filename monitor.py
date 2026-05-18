#!/usr/bin/env python3
"""
SUSDAT 价格监控 - GitHub Actions 云端版
每 5 分钟运行一次，电脑关机也能监控
"""

import os
import requests
import json
from datetime import datetime

# ============================================================
#  配置（从 GitHub Secrets 读取，无需修改）
# ============================================================
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
GIST_TOKEN         = os.environ["GIST_TOKEN"]   # 用于存储上次价格
GIST_ID            = os.environ["GIST_ID"]       # Gist ID

CONTRACT     = "0xd166337499e176bbc38a1fbd113ab144e5bd2df7"
TOKEN_SYMBOL = "SUSDAT"

DROP_THRESHOLD_PCT = 0.1   # 跌超 0.1% 通知
RISE_THRESHOLD_PCT = 5.0   # 涨超 5% 通知
BROADCAST_EVERY_N_RUNS = 6 # 每 6 次运行（约 30 分钟）播报一次

# ============================================================
#  工具函数
# ============================================================

def now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

def fmt(p):
    if p < 0.0001:   return f"${p:.8f}"
    elif p < 0.01:   return f"${p:.6f}"
    elif p < 1:      return f"${p:.4f}"
    else:            return f"${p:.4f}"

def get_price():
    url = f"https://api.dexscreener.com/latest/dex/tokens/{CONTRACT}"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    pairs = r.json().get("pairs") or []
    if not pairs:
        return None, {}
    pair = sorted(pairs, key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0), reverse=True)[0]
    return float(pair["priceUsd"]), {
        "change_24h":  pair.get("priceChange", {}).get("h24", 0),
        "change_5m":   pair.get("priceChange", {}).get("m5", 0),
        "volume_24h":  (pair.get("volume") or {}).get("h24", 0),
        "liquidity":   (pair.get("liquidity") or {}).get("usd", 0),
        "dex":         pair.get("dexId", ""),
    }

def send_tg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    print("TG:", r.status_code, r.text[:100])

# ============================================================
#  Gist：持久化存储上次价格 & 运行计数
# ============================================================

GIST_FILENAME = "susdat_state.json"
HEADERS = {"Authorization": f"token {GIST_TOKEN}", "Accept": "application/vnd.github+json"}

def load_state():
    r = requests.get(f"https://api.github.com/gists/{GIST_ID}", headers=HEADERS, timeout=10)
    content = r.json()["files"][GIST_FILENAME]["content"]
    return json.loads(content)

def save_state(state):
    payload = {"files": {GIST_FILENAME: {"content": json.dumps(state)}}}
    requests.patch(f"https://api.github.com/gists/{GIST_ID}", headers=HEADERS, json=payload, timeout=10)

# ============================================================
#  主逻辑
# ============================================================

def main():
    # 读取当前价格
    try:
        price, info = get_price()
    except Exception as e:
        send_tg(f"⚠️ <b>{TOKEN_SYMBOL} 监控报错</b>\n无法获取价格: {e}\n⏱ {now()}")
        return

    if price is None:
        send_tg(f"⚠️ <b>{TOKEN_SYMBOL}</b> 未找到交易对数据，请确认合约或链正确\n⏱ {now()}")
        return

    print(f"当前价格: {fmt(price)}")

    # 读取上次状态
    try:
        state = load_state()
    except Exception:
        # 首次运行，初始化 Gist
        state = {"last_price": price, "last_notified_price": price, "run_count": 0}
        save_state(state)
        send_tg(
            f"🚀 <b>{TOKEN_SYMBOL} 云端监控已启动</b>\n\n"
            f"💰 当前价格: <b>{fmt(price)}</b>\n"
            f"📊 24H 涨跌: {info['change_24h']}%\n"
            f"💧 流动性: ${float(info['liquidity']):,.0f}\n"
            f"🏦 交易所: {info['dex']}\n\n"
            f"⚙️ 跌幅通知 ≥{DROP_THRESHOLD_PCT}%  |  涨幅通知 ≥{RISE_THRESHOLD_PCT}%\n"
            f"⏱ {now()}"
        )
        return

    last_price          = float(state["last_price"])
    last_notified_price = float(state["last_notified_price"])
    run_count           = int(state.get("run_count", 0)) + 1

    # 计算涨跌
    def pct(old, new):
        return (new - old) / old * 100 if old else 0

    change_from_notified = pct(last_notified_price, price)
    change_from_last     = pct(last_price, price)

    # 跌幅报警
    if change_from_notified <= -DROP_THRESHOLD_PCT:
        send_tg(
            f"🔴 <b>{TOKEN_SYMBOL} 价格下跌提醒</b>\n\n"
            f"💰 当前价格: <b>{fmt(price)}</b>\n"
            f"⬇️ 跌幅: <b>{change_from_notified:.2f}%</b>\n"
            f"📊 24H 涨跌: {info['change_24h']}%\n"
            f"💧 流动性: ${float(info['liquidity']):,.0f}\n"
            f"⏱ {now()}"
        )
        last_notified_price = price

    # 涨幅报警
    elif RISE_THRESHOLD_PCT > 0 and change_from_notified >= RISE_THRESHOLD_PCT:
        send_tg(
            f"🟢 <b>{TOKEN_SYMBOL} 价格上涨提醒</b>\n\n"
            f"💰 当前价格: <b>{fmt(price)}</b>\n"
            f"⬆️ 涨幅: <b>+{change_from_notified:.2f}%</b>\n"
            f"📊 24H 涨跌: {info['change_24h']}%\n"
            f"💧 流动性: ${float(info['liquidity']):,.0f}\n"
            f"⏱ {now()}"
        )
        last_notified_price = price

    # 定时播报（每 ~30 分钟）
    if run_count % BROADCAST_EVERY_N_RUNS == 0:
        emoji = "🟢" if change_from_last >= 0 else "🔴"
        send_tg(
            f"📊 <b>{TOKEN_SYMBOL} 定时播报</b>\n\n"
            f"💰 当前价格: <b>{fmt(price)}</b>\n"
            f"{emoji} 30min 涨跌: {change_from_last:+.2f}%\n"
            f"📊 24H 涨跌: {info['change_24h']}%\n"
            f"💧 流动性: ${float(info['liquidity']):,.0f}\n"
            f"📈 24H 成交量: ${float(info['volume_24h']):,.0f}\n"
            f"⏱ {now()}"
        )

    # 保存新状态
    save_state({
        "last_price": price,
        "last_notified_price": last_notified_price,
        "run_count": run_count
    })

if __name__ == "__main__":
    main()
