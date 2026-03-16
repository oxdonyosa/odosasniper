import os
import time
import json
import requests
from datetime import datetime

# ── CONFIG ──────────────────────────────────────────────
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHAT_ID     = os.environ.get("CHAT_ID",   "YOUR_CHAT_ID_HERE")
MIN_PROFIT  = float(os.environ.get("MIN_PROFIT", "15"))   # minimum % profit to alert
SCAN_EVERY  = int(os.environ.get("SCAN_EVERY",   "1800")) # seconds between scans (30 min)
MAX_SIGNALS = int(os.environ.get("MAX_SIGNALS",  "5"))    # max signals per scan
# ────────────────────────────────────────────────────────

GAMMA_URL = (
    "https://gamma-api.polymarket.com/markets"
    "?active=true&closed=false&limit=100"
    "&order=volumeNum&ascending=false"
)

CATS = {
    "🏛 POLITICS": ["trump","biden","election","president","senate","congress",
                    "vote","democrat","republican","governor","ayatollah",
                    "khamenei","ukraine","russia","nato","tariff","executive",
                    "supreme","minister","parliament"],
    "🪙 CRYPTO":   ["bitcoin","btc","ethereum","eth","crypto","solana","sol",
                    "xrp","ripple","coinbase","binance","altcoin","defi",
                    "nft","blockchain","token"],
    "🏆 SPORTS":   ["nba","nfl","mlb","nhl","ufc","tennis","golf","soccer",
                    "super bowl","championship","playoff","world cup",
                    "match","league","fight"],
    "📈 FINANCE":  ["stock","s&p","nasdaq","dow","fed","interest rate",
                    "inflation","gdp","recession","economy","market cap",
                    "ipo","earnings","bond","yield"],
}

def get_category(question: str) -> str:
    t = question.lower()
    for cat, keywords in CATS.items():
        if any(k in t for k in keywords):
            return cat
    return "🎯 OTHER"

def fmt_volume(v: float) -> str:
    if not v:      return "$0"
    if v >= 1e6:   return f"${v/1e6:.1f}M"
    if v >= 1e3:   return f"${v/1e3:.0f}K"
    return f"${v:.0f}"

def fmt_date(d: str) -> str:
    if not d:
        return "—"
    try:
        dt   = datetime.fromisoformat(d.replace("Z", "+00:00"))
        now  = datetime.now(dt.tzinfo)
        days = (dt - now).days
        if days <= 0:  return "Ends today"
        if days == 1:  return "1d left"
        if days <= 7:  return f"{days}d left"
        if days <= 30: return f"{round(days/7)}w left"
        return dt.strftime("%b %d")
    except Exception:
        return "—"

def fetch_signals() -> list[dict]:
    resp = requests.get(GAMMA_URL, timeout=15)
    resp.raise_for_status()
    markets = resp.json()

    signals = []
    for m in markets:
        if not all(k in m for k in ("question", "outcomePrices", "outcomes")):
            continue
        try:
            prices   = json.loads(m["outcomePrices"]) if isinstance(m["outcomePrices"], str) else m["outcomePrices"]
            outcomes = json.loads(m["outcomes"])       if isinstance(m["outcomes"], str)       else m["outcomes"]
        except Exception:
            continue

        if not isinstance(prices, list) or len(prices) < 2:
            continue

        best_bid  = float(m.get("bestBid")  or 0) or None
        best_ask  = float(m.get("bestAsk")  or 0) or None
        spread    = float(m.get("spread")   or 0) or None
        last_trade= float(m.get("lastTradePrice") or 0) or None

        for i, raw_price in enumerate(prices):
            gp = float(raw_price)
            if gp <= 0.03 or gp >= 0.94:
                continue

            fill = best_ask if (best_ask and 0.03 < best_ask < 0.97) else gp
            profit = round((1 / fill - 1) * 100, 1)
            if profit < MIN_PROFIT:
                continue

            edge = round(gp - best_ask, 4) if best_ask else None
            signals.append({
                "question":   m["question"],
                "outcome":    outcomes[i] if i < len(outcomes) else "YES",
                "gamma_price":gp,
                "gamma_pct":  round((1 / gp - 1) * 100, 1),
                "fill_price": fill,
                "profit":     profit,
                "best_bid":   best_bid,
                "best_ask":   best_ask,
                "spread":     spread,
                "last_trade": last_trade,
                "has_clob":   bool(best_ask and best_bid),
                "edge":       edge,
                "category":   get_category(m["question"]),
                "volume":     m.get("volumeNum") or 0,
                "liquidity":  m.get("liquidityNum") or 0,
                "end_date":   m.get("endDate", ""),
                "slug":       m.get("slug", ""),
            })

    signals.sort(key=lambda x: x["profit"], reverse=True)
    return signals[:MAX_SIGNALS]

def build_message(signals: list[dict], scan_num: int) -> str:
    now = datetime.now().strftime("%b %d · %H:%M")
    lines = [
        f"🎯 *Polymarket Sniper Bot*",
        f"📡 Scan #{scan_num} · {now}",
        f"━━━━━━━━━━━━━━━━━━━━━━",
    ]

    for i, s in enumerate(signals, 1):
        profit_bar = "🟢" if s["profit"] >= 40 else "🟡" if s["profit"] >= 15 else "🔵"
        clob_tag   = " · `CLOB LIVE`" if s["has_clob"] else ""
        edge_tag   = ""
        if s["edge"] is not None:
            if s["edge"] > 0.005:
                edge_tag = f"  ┗ Edge vs gamma: `+{s['edge']}`"
            elif s["edge"] < -0.005:
                edge_tag = f"  ┗ Slippage vs gamma: `{s['edge']}`"

        lines += [
            f"",
            f"{profit_bar} *#{i} · {s['category']}{clob_tag}*",
            f"━━━━━━━━━━━━━━━━━━━━━━",
            f"📌 {s['question']}",
            f"",
            f"  • Outcome: `{s['outcome']}`",
            f"  • Gamma price: `{round(s['gamma_price']*100)}¢` → `+{s['gamma_pct']}%`",
        ]
        if s["has_clob"]:
            lines.append(f"  • CLOB best ask: `{round(s['best_ask']*100)}¢` → *+{s['profit']}%*")
            lines.append(f"  • Order book: Bid `{round(s['best_bid']*100)}¢` | Ask `{round(s['best_ask']*100)}¢`")
            if s["spread"]:
                lines.append(f"  • Spread: `{round(s['spread'], 3)}`")
        else:
            lines.append(f"  • Profit potential: *+{s['profit']}%*")
        if edge_tag:
            lines.append(edge_tag)
        lines += [
            f"  • Volume: `{fmt_volume(s['volume'])}` · Expires: `{fmt_date(s['end_date'])}`",
            f"  • [Trade on Polymarket](https://polymarket.com/event/{s['slug']})",
        ]

    lines += [
        f"",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"_Next scan in {SCAN_EVERY//60} min · Min profit: +{MIN_PROFIT}%_",
    ]
    return "\n".join(lines)

def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id":    CHAT_ID,
        "text":       text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=payload, timeout=10)
    r.raise_for_status()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Message sent ✓")

def main():
    print("=" * 50)
    print("  Polymarket Sniper Bot — starting up")
    print(f"  Min profit : +{MIN_PROFIT}%")
    print(f"  Scan every : {SCAN_EVERY // 60} minutes")
    print(f"  Max signals: {MAX_SIGNALS}")
    print("=" * 50)

    scan_num = 1
    while True:
        try:
            print(f"\n[Scan #{scan_num}] Fetching markets...")
            signals = fetch_signals()
            print(f"[Scan #{scan_num}] Found {len(signals)} signals above +{MIN_PROFIT}%")

            if signals:
                msg = build_message(signals, scan_num)
                send_telegram(msg)
            else:
                print(f"[Scan #{scan_num}] No signals — skipping message")

        except Exception as e:
            print(f"[ERROR] {e}")

        scan_num += 1
        print(f"Sleeping {SCAN_EVERY // 60} min until next scan...")
        time.sleep(SCAN_EVERY)

if __name__ == "__main__":
    main()
