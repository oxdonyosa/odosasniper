import os
import time
import json
import requests
from datetime import datetime, timezone

# ── CONFIG ──────────────────────────────────────────────
BOT_TOKEN      = os.environ.get("BOT_TOKEN",      "YOUR_BOT_TOKEN_HERE")
CHAT_ID        = os.environ.get("CHAT_ID",        "YOUR_CHAT_ID_HERE")
SCAN_EVERY     = int(os.environ.get("SCAN_EVERY", "3600"))
MAX_SIGNALS    = int(os.environ.get("MAX_SIGNALS","5"))

# ── CONVICTION FILTER SETTINGS ───────────────────────────
MIN_PRICE      = 0.70   # outcome must be 70c+ (70%+ probability)
MAX_PRICE      = 0.97   # avoid 97c+ (too little edge left)
MAX_HOURS      = 720    # 30 days — wide net, expiry shown as info only
MIN_VOLUME     = 3000   # minimum $3k volume
MIN_PROFIT_PCT = 10.0   # minimum profit %
# ────────────────────────────────────────────────────────

GAMMA_URL = (
    "https://gamma-api.polymarket.com/markets"
    "?active=true&closed=false&limit=500"
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

sent_signals: set = set()
scan_num = 0

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

def hours_left(end_date: str):
    if not end_date:
        return None
    try:
        dt  = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return max((dt - now).total_seconds() / 3600, 0)
    except Exception:
        return None

def fmt_expiry(hrs) -> str:
    if hrs is None:  return "—"
    if hrs <= 1:     return f"{int(hrs*60)}min left"
    if hrs <= 24:    return f"{hrs:.1f}hrs left"
    return f"{hrs/24:.1f}d left"

def conviction_score(price: float, hrs: float, volume: float, profit: float) -> float:
    price_score   = price * 40
    urgency_score = max(0, (48 - hrs) / 48 * 30)
    volume_score  = min(volume / 10000 * 20, 20)
    profit_score  = min(profit / 20 * 10, 10)
    return round(price_score + urgency_score + volume_score + profit_score, 2)

def signal_key(question: str, outcome: str) -> str:
    return f"{question.strip().lower()}|{outcome.strip().lower()}"

def fetch_signals() -> list:
    resp = requests.get(GAMMA_URL, timeout=15)
    resp.raise_for_status()
    markets = resp.json()

    signals = []
    skipped_volume = skipped_price = skipped_profit = skipped_expiry = 0
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

        volume    = float(m.get("volumeNum")    or 0)
        liquidity = float(m.get("liquidityNum") or 0)
        if volume < MIN_VOLUME:
            skipped_volume += 1
            continue

        best_bid   = float(m.get("bestBid")        or 0) or None
        best_ask   = float(m.get("bestAsk")        or 0) or None
        spread     = float(m.get("spread")         or 0) or None
        last_trade = float(m.get("lastTradePrice") or 0) or None
        end_date   = m.get("endDate", "")
        hrs        = hours_left(end_date)

        # Only skip if we KNOW it expires beyond MAX_HOURS
        # If hrs is None (no endDate) — let it through
        if hrs is not None and (hrs > MAX_HOURS or hrs <= 0):
            continue

        for i, raw_price in enumerate(prices):
            gp = float(raw_price)
            if gp < MIN_PRICE or gp > MAX_PRICE:
                continue

            fill   = best_ask if (best_ask and MIN_PRICE <= best_ask <= MAX_PRICE) else gp
            profit = round((1 / fill - 1) * 100, 2)
            if profit < MIN_PROFIT_PCT:
                continue

            outcome = outcomes[i] if i < len(outcomes) else "YES"
            key     = signal_key(m["question"], outcome)
            if key in sent_signals:
                continue

            edge  = round(gp - best_ask, 4) if best_ask else None
            score = conviction_score(fill, hrs, volume, profit)

            signals.append({
                "key":         key,
                "question":    m["question"],
                "outcome":     outcome,
                "gamma_price": gp,
                "fill_price":  fill,
                "profit":      profit,
                "best_bid":    best_bid,
                "best_ask":    best_ask,
                "spread":      spread,
                "last_trade":  last_trade,
                "has_clob":    bool(best_ask and best_bid),
                "edge":        edge,
                "category":    get_category(m["question"]),
                "volume":      volume,
                "liquidity":   liquidity,
                "end_date":    end_date,
                "hrs_left":    hrs,
                "slug":        m.get("slug", ""),
                "score":       score,
            })

    print(f"[DEBUG] Skipped by volume: {skipped_volume} | Passed price+profit filter: {len(signals)}")
    signals.sort(key=lambda x: x["score"], reverse=True)
    return signals[:MAX_SIGNALS]

def probability_bar(price: float) -> str:
    pct    = int(price * 10)
    filled = "█" * pct
    empty  = "░" * (10 - pct)
    return f"{filled}{empty} {round(price*100)}%"

def build_message(signals: list) -> str:
    global scan_num
    now = datetime.now().strftime("%b %d · %H:%M")
    lines = [
        f"🎯 *Polymarket Conviction Sniper*",
        f"📡 Scan #{scan_num} · {now}",
        f"⚡ High-probability · Sub-48hr plays",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    for i, s in enumerate(signals, 1):
        urgency = "🔴 URGENT" if s["hrs_left"] <= 6 else "🟠 TODAY" if s["hrs_left"] <= 24 else "🟡 SOON"
        clob    = " · `CLOB ✓`" if s["has_clob"] else ""

        lines += [
            f"",
            f"*#{i} · {s['category']}{clob}*",
            f"━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📌 _{s['question']}_",
            f"",
            f"  {urgency} · `{fmt_expiry(s['hrs_left'])}`",
            f"",
            f"  📊 `{probability_bar(s['fill_price'])}`",
            f"  ✅ Outcome:    `{s['outcome']}`",
            f"  💰 Entry:      `{round(s['fill_price']*100)}¢`",
            f"  📈 Profit:     *+{s['profit']}%*",
            f"  🏆 Score:      `{s['score']}/100`",
        ]
        if s["has_clob"]:
            lines += [
                f"",
                f"  📖 Bid `{round(s['best_bid']*100)}¢` | Ask `{round(s['best_ask']*100)}¢`",
            ]
            if s["spread"]:
                lines.append(f"  Spread: `{round(s['spread'], 3)}`")
        lines += [
            f"",
            f"  💵 Vol: `{fmt_volume(s['volume'])}`",
            f"  🔗 [Trade →](https://polymarket.com/event/{s['slug']})",
        ]

    lines += [
        f"",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        f"_Filters: {MIN_PRICE*100:.0f}–{MAX_PRICE*100:.0f}% prob · ≤{MAX_HOURS}hrs · Min ${MIN_VOLUME:,} vol_",
        f"_Next scan in {SCAN_EVERY//60} min_",
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
    print("=" * 55)
    print("  Polymarket Conviction Sniper — starting up")
    print(f"  Probability : {MIN_PRICE*100:.0f}% – {MAX_PRICE*100:.0f}%")
    print(f"  Max expiry  : {MAX_HOURS} hours")
    print(f"  Min volume  : ${MIN_VOLUME:,}")
    print(f"  Min profit  : +{MIN_PROFIT_PCT}%")
    print(f"  Scan every  : {SCAN_EVERY//60} minutes")
    print("=" * 55)

    last_reset = time.time()

    while True:
        scan_num += 1
        if time.time() - last_reset > 86400:
            sent_signals.clear()
            last_reset = time.time()
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Memory reset")

        try:
            print(f"\n[Scan #{scan_num}] Scanning for conviction plays...")
            signals = fetch_signals()
            print(f"[Scan #{scan_num}] Found {len(signals)} signals")

            if signals:
                msg = build_message(signals)
                send_telegram(msg)
                for s in signals:
                    sent_signals.add(s["key"])
                print(f"[Scan #{scan_num}] Sent. Memory: {len(sent_signals)} seen")
            else:
                print(f"[Scan #{scan_num}] No signals passed filters")

        except Exception as e:
            print(f"[ERROR] {e}")

        print(f"Sleeping {SCAN_EVERY//60} min...")
        time.sleep(SCAN_EVERY)

if __name__ == "__main__":
    main()
