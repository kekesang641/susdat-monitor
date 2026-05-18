#!/usr/bin/env python3
"""
SUSDAT + STRC 双资产监控 - GitHub Actions 云端版
- SUSDAT: 链上稳定币，监控脱锚风险（应始终≈$1）
- STRC: Strategy优先股（纳斯达克），监控股价下跌
"""

import os
import requests
import json
from datetime import datetime

# ============================================================
#  配置（从 GitHub Secrets 读取）
# ============================================================
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
GIST_TOKEN         = os.environ["GIST_TOKEN"]
GIST_ID            = os.environ["GIST_ID"]

# SUSDAT 合约（Ethereum）
SUSDAT_CONTRACT = "0xd166337499e176bbc38a1fbd113ab144e5bd2df7"

# ---- 告警阈值 ----
SUSDAT_DROP_PCT    = 0.1    # SUSDAT 下跌提醒（%）
SUSDAT_DEPEG_LOW   = 0.99   # SUSDAT 低于此价格视为脱锚（$）
SUSDAT_DEPEG_HIGH  = 1.01   # SUSDAT 高于此价格视为溢价（$）

STRC_DROP_PCT      = 2.0    # STRC 下跌提醒（%）
STRC_RISE_PCT      = 3.0    # STRC 上涨提醒（%）

# 定时播报：每 N 次运行播报一次（每次5分钟，6次=30分钟）
BROADCAST_EVERY_N  = 3   # 3次×5分钟 = 每15分钟播报一次

# ============================================================
#  工具函数
# ============================================================
def now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

def fmt(p):
    if p is None: return "N/A"
    if p < 0.01:   return f"${p:.6f}"
    elif p < 1:    return f"${p:.4f}"
    else:          return f"${p:.2f}"

def pct(old, new):
    if not old: return 0
    return (new - old) / old * 100

def send_tg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    print("TG:", r.status_code)

# ============================================================
#  获取 SUSDAT 价格（DexScreener）
# ============================================================
def get_susdat():
    url = f"https://api.dexscreener.com/latest/dex/tokens/{SUSDAT_CONTRACT}"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    pairs = r.json().get("pairs") or []
    if not pairs:
        return None, {}
    pair = sorted(pairs, key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0), reverse=True)[0]
    return float(pair["priceUsd"]), {
        "change_24h": pair.get("priceChange", {}).get("h24", 0),
        "liquidity":  (pair.get("liquidity") or {}).get("usd", 0),
        "volume_24h": (pair.get("volume") or {}).get("h24", 0),
        "dex":        pair.get("dexId", ""),
    }

# ============================================================
#  获取 STRC 股价（Yahoo Finance）
# ============================================================
def get_strc():
    try:
        import yfinance as yf
        ticker = yf.Ticker("STRC")
        info = ticker.fast_info
        price = info.last_price
        prev_close = info.previous_close
        change_pct = pct(prev_close, price) if prev_close else 0
        return float(price), {
            "prev_close":  float(prev_close) if prev_close else None,
            "change_pct":  round(change_pct, 2),
            "market_cap":  getattr(info, "market_cap", None),
        }
    except Exception as e:
        print(f"STRC 获取失败: {e}")
        return None, {}

# ============================================================
#  Gist 状态持久化
# ============================================================
SCRIPT_VERSION = "2.1"   # 版本变更时自动重置状态并发送启动通知
GIST_FILE = "susdat_state.json"
HEADERS   = {"Authorization": f"token {GIST_TOKEN}", "Accept": "application/vnd.github+json"}

def load_state():
    r = requests.get(f"https://api.github.com/gists/{GIST_ID}", headers=HEADERS, timeout=10)
    content = r.json()["files"][GIST_FILE]["content"]
    return json.loads(content)

def save_state(s):
    requests.patch(f"https://api.github.com/gists/{GIST_ID}",
                   headers=HEADERS, json={"files": {GIST_FILE: {"content": json.dumps(s)}}}, timeout=10)

# ============================================================
#  主逻辑
# ============================================================
def main():
    # 获取两个价格
    susdat_price, susdat_info = get_susdat()
    strc_price,   strc_info   = get_strc()

    print(f"SUSDAT: {fmt(susdat_price)}  |  STRC: {fmt(strc_price)}")

    # 读取历史状态
    try:
        state = load_state()
        is_first_run = not state or state.get("version") != SCRIPT_VERSION
    except Exception:
        is_first_run = True
        state = {}

    if is_first_run:
        state = {
            "version": SCRIPT_VERSION,
            "susdat_last": susdat_price,
            "susdat_notified": susdat_price,
            "strc_last": strc_price,
            "strc_notified": strc_price,
            "run_count": 0,
        }
        save_state(state)

        # 首次运行通知
        strc_line = f"📈 STRC 股价: <b>{fmt(strc_price)}</b>  ({strc_info.get('change_pct', 0):+.2f}% 今日)\n" if strc_price else "📈 STRC: 美股休市中\n"
        send_tg(
            f"🚀 <b>双资产监控已启动</b>\n\n"
            f"💰 SUSDAT: <b>{fmt(susdat_price)}</b>  (应≈$1.00)\n"
            f"📊 SUSDAT 24H: {susdat_info.get('change_24h', 0)}%\n"
            f"💧 流动性: ${float(susdat_info.get('liquidity', 0)):,.0f}\n\n"
            f"{strc_line}"
            f"\n⚙️ SUSDAT跌幅 ≥{SUSDAT_DROP_PCT}%  |  STRC跌幅 ≥{STRC_DROP_PCT}%\n"
            f"⚠️ 脱锚预警: SUSDAT {'<$'+str(SUSDAT_DEPEG_LOW)} 或 {'>$'+str(SUSDAT_DEPEG_HIGH)}\n"
            f"⏱ {now()}"
        )
        return

    run_count         = int(state.get("run_count", 0)) + 1
    susdat_last       = float(state.get("susdat_last") or susdat_price or 1)
    susdat_notified   = float(state.get("susdat_notified") or susdat_price or 1)
    strc_last         = float(state["strc_last"]) if state.get("strc_last") else strc_price
    strc_notified     = float(state["strc_notified"]) if state.get("strc_notified") else strc_price

    alerts = []

    # ---- SUSDAT 检查 ----
    if susdat_price:
        susdat_chg = pct(susdat_notified, susdat_price)

        # 脱锚预警（优先级最高）
        if susdat_price < SUSDAT_DEPEG_LOW:
            alerts.append(
                f"🚨 <b>SUSDAT 脱锚警报！</b>\n\n"
                f"💰 当前: <b>{fmt(susdat_price)}</b>（低于 ${SUSDAT_DEPEG_LOW}）\n"
                f"⬇️ 跌幅: <b>{susdat_chg:.3f}%</b>\n"
                f"💧 流动性: ${float(susdat_info.get('liquidity',0)):,.0f}\n"
                f"⚠️ 建议关注风险！\n⏱ {now()}"
            )
            state["susdat_notified"] = susdat_price

        elif susdat_price > SUSDAT_DEPEG_HIGH:
            alerts.append(
                f"⚠️ <b>SUSDAT 价格溢价</b>\n\n"
                f"💰 当前: <b>{fmt(susdat_price)}</b>（高于 ${SUSDAT_DEPEG_HIGH}）\n"
                f"⬆️ 涨幅: <b>+{susdat_chg:.3f}%</b>\n⏱ {now()}"
            )
            state["susdat_notified"] = susdat_price

        elif susdat_chg <= -SUSDAT_DROP_PCT:
            alerts.append(
                f"🔴 <b>SUSDAT 价格下跌</b>\n\n"
                f"💰 当前: <b>{fmt(susdat_price)}</b>\n"
                f"⬇️ 跌幅: <b>{susdat_chg:.3f}%</b>\n"
                f"📊 24H: {susdat_info.get('change_24h',0)}%\n⏱ {now()}"
            )
            state["susdat_notified"] = susdat_price

    # ---- STRC 检查 ----
    if strc_price and strc_notified:
        strc_chg = pct(strc_notified, strc_price)

        if strc_chg <= -STRC_DROP_PCT:
            alerts.append(
                f"🔴 <b>STRC 股价下跌提醒</b>\n\n"
                f"📉 当前: <b>{fmt(strc_price)}</b>\n"
                f"⬇️ 跌幅: <b>{strc_chg:.2f}%</b>\n"
                f"📊 今日涨跌: {strc_info.get('change_pct', 0):+.2f}%\n⏱ {now()}"
            )
            state["strc_notified"] = strc_price

        elif strc_chg >= STRC_RISE_PCT:
            alerts.append(
                f"🟢 <b>STRC 股价上涨提醒</b>\n\n"
                f"📈 当前: <b>{fmt(strc_price)}</b>\n"
                f"⬆️ 涨幅: <b>+{strc_chg:.2f}%</b>\n"
                f"📊 今日涨跌: {strc_info.get('change_pct', 0):+.2f}%\n⏱ {now()}"
            )
            state["strc_notified"] = strc_price

    # 发送告警
    for alert in alerts:
        send_tg(alert)

    # ---- 定时播报 ----
    if run_count % BROADCAST_EVERY_N == 0:
        susdat_chg_30 = pct(susdat_last, susdat_price) if susdat_price else 0
        strc_chg_30   = pct(strc_last, strc_price) if (strc_price and strc_last) else 0
        e1 = "🟢" if susdat_chg_30 >= 0 else "🔴"
        e2 = "🟢" if strc_chg_30 >= 0 else "🔴"
        strc_broadcast = f"{e2} STRC: <b>{fmt(strc_price)}</b>  ({strc_chg_30:+.2f}% / 30min)\n" if strc_price else "📈 STRC: 美股休市中\n"
        send_tg(
            f"📊 <b>双资产定时播报</b>\n\n"
            f"{e1} SUSDAT: <b>{fmt(susdat_price)}</b>  ({susdat_chg_30:+.3f}% / 30min)\n"
            f"   流动性: ${float(susdat_info.get('liquidity',0)):,.0f}\n\n"
            f"{strc_broadcast}"
            f"\n⏱ {now()}"
        )

    # 保存状态
    state["version"]     = SCRIPT_VERSION
    state["susdat_last"] = susdat_price
    state["strc_last"]   = strc_price
    state["run_count"]   = run_count
    save_state(state)

if __name__ == "__main__":
    main()
