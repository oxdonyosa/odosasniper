import os
import time
import json
import requests
from datetime import datetime

# ── CONFIG ──────────────────────────────────────────────
BOT_TOKEN   = os.environ.get("BOT_TOKEN",   "YOUR_BOT_TOKEN_HERE")
CHAT_ID     = os.environ.get("CHAT_ID",     "YOUR_CHAT_ID_HERE")
MIN_PROFIT  = float(os.environ.get("MIN_PROFIT",  "15"))
SCAN_EVERY  = int(os.environ.get("SCAN_EVERY",    "1800"))
MAX_SIGNALS = int(os.environ.get("MAX_SIGNALS",   "5"))
# ────────────────────────────────────────────────────────

GAMMA_URL = (
    "https://gamma-api.polymarket.com/markets"
    "?active=true&closed=false&limit=200"
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

# ── MEMORY: tracks what we've already sent ──────────────
sent_signals: set = set()   # stores question+outcome keys
scan_num = 0
# ────────────────────────────────────────────────────────

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

def signal_key(question: str, outcome: str) -> str:
    return f"{question.strip().lower()}|{outcome.strip().lower()}"

def fetch_signals() -> list[dict]:
    resp = requests.get(GAMMA_URL, timeout=15)
    resp.raise_for_status()
    markets = resp.json()

    # Group by category so we pick diverse signals
    by_cat: dict[str, list] = {}

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

        best_bid   = float(m.get("bestBid")        or 0) or None
        best_ask   = float(m.get("bestAsk")        or 0) or None
        spread     = float(m.get("spread")         or 0) or None
        last_trade = float(m.get("lastTradePrice") or 0) or None
        volume     = m.get("volumeNum")  or 0
        liquidity  = m.get("liquidityNum") or 0

        # Skip very low liquidity markets — low quality signals
        if volume < 500:
            continue

        for i, raw_price in enumerate(prices):
            gp = float(raw_price)
            if gp <= 0.03 or gp >= 0.94:
                continue

            fill   = best_ask if (best_ask and 0.03 < best_ask < 0.97) else gp
            profit = round((1 / fill - 1) * 100, 1)
            if profit < MIN_PROFIT:
                continue

            outcome = outcomes[i] if i < len(outcomes) else "YES"
            key     = signal_key(m["question"], outcome)

            # Skip if already sent this exact signal before
            if key in sent_signals:
                continue

            edge = round(gp - best_ask, 4) if best_ask else None
            cat  = get_category(m["question"])

            sig = {
                "key":        key,
                "question":   m["question"],
                "outcome":    outcome,
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
                "category":   cat,
                "volume":     volume,
                "liquidity":  liquidity,
                "end_date":   m.get("endDate", ""),
                "slug":       m.get("slug", ""),
            }

            if cat not in by_cat:
                by_cat[cat] = []
            by_cat[cat].append(sig)

    # Sort each category by profit descending
    for cat in by_cat:
        by_cat[cat].sort(key=lambda x: x["profit"], reverse=True)

    # Pick diverse signals — rotate across categories
    final = []
    cats  = list(by_cat.keys())
    idx   = 0
    while len(final) < MAX_SIGNALS and any(by_cat[c] for c in cats):
        cat = cats[idx % len(cats)]
        if by_cat.get(cat):
            final.append(by_cat[cat].pop(0))
        idx += 1

    # Sort final list by profit
    final.sort(key=lambda x: x["profit"], reverse=True)
    return final

def build_message(signals: list[dict]) -> str:
    global scan_num
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
                edge_tag = f"  ┗ Slippage: `{s['edge']}`"

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
    url     = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
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
    global scan_num, sent_signals
    print("=" * 50)
    print("  Polymarket Sniper Bot — starting up")
    print(f"  Min profit : +{MIN_PROFIT}%")
    print(f"  Scan every : {SCAN_EVERY // 60} minutes")
    print(f"  Max signals: {MAX_SIGNALS}")
    print("  Deduplication: ON")
    print("  Category diversity: ON")
    print("=" * 50)

    # Clear memory every 24 hours so old signals can resurface if still valid
    last_reset = time.time()

    while True:
        scan_num += 1

        # Reset seen signals every 24 hours
        if time.time() - last_reset > 86400:
            sent_signals.clear()
            last_reset = time.time()
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Memory reset — fresh slate")

        try:
            print(f"\n[Scan #{scan_num}] Fetching markets...")
            signals = fetch_signals()
            print(f"[Scan #{scan_num}] Found {len(signals)} new distinct signals")

            if signals:
                msg = build_message(signals)
                send_telegram(msg)
                # Mark these signals as sent so they don't repeat
                for s in signals:
                    sent_signals.add(s["key"])
                print(f"[Scan #{scan_num}] Memory now holds {len(sent_signals)} seen signals")
            else:
                print(f"[Scan #{scan_num}] No new signals — skipping message")

        except Exception as e:
            print(f"[ERROR] {e}")

        print(f"Sleeping {SCAN_EVERY // 60} min...")
        time.sleep(SCAN_EVERY)

if __name__ == "__main__":
    main()
