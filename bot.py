import os
import time
import json
import requests
from datetime import datetime, timezone

# ── CONFIG ────────────────────────────────────────────
BOT_TOKEN      = os.environ.get("BOT_TOKEN",      "YOUR_BOT_TOKEN_HERE")
CHAT_ID        = os.environ.get("CHAT_ID",        "YOUR_CHAT_ID_HERE")
SCAN_EVERY     = int(os.environ.get("SCAN_EVERY", "900"))
MAX_SIGNALS    = int(os.environ.get("MAX_SIGNALS","5"))

MIN_PRICE      = 0.70
MAX_PRICE      = 0.97
MIN_VOLUME     = 3000
MIN_PROFIT_PCT = 10.0
# ──────────────────────────────────────────────────────

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

sent_signals = set()
scan_num = 0

def get_category(question):
    t = question.lower()
    for cat, keywords in CATS.items():
        if any(k in t for k in keywords):
            return cat
    return "🎯 OTHER"

def fmt_volume(v):
    if not v:     return "$0"
    if v >= 1e6:  return f"${v/1e6:.1f}M"
    if v >= 1e3:  return f"${v/1e3:.0f}K"
    return f"${v:.0f}"

def hours_left(end_date):
    if not end_date:
        return None
    try:
        dt  = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        h   = (dt - now).total_seconds() / 3600
        return max(h, 0)
    except Exception:
        return None

def fmt_expiry(hrs):
    if hrs is None: return "—"
    if hrs <= 1:    return f"{int(hrs*60)}min left"
    if hrs <= 24:   return f"{hrs:.1f}hrs left"
    return f"{hrs/24:.1f}d left"

def conviction_score(price, hrs, volume, profit):
    price_score  = price * 40
    # hrs could be None — treat as neutral 50hrs if unknown
    safe_hrs     = hrs if hrs is not None else 50
    urgency      = max(0, (72 - safe_hrs) / 72 * 30)
    volume_score = min(volume / 10000 * 20, 20)
    profit_score = min(profit / 20 * 10, 10)
    return round(price_score + urgency + volume_score + profit_score, 2)

def signal_key(question, outcome):
    return f"{question.strip().lower()}|{outcome.strip().lower()}"

def fetch_signals():
    resp = requests.get(GAMMA_URL, timeout=20)
    resp.raise_for_status()
    markets = resp.json()

    print(f"  [DEBUG] Fetched {len(markets)} markets from Gamma")

    signals = []
    skip_vol = skip_price = skip_profit = 0

    for m in markets:
        if not m.get("question") or not m.get("outcomePrices") or not m.get("outcomes"):
            continue

        try:
            prices   = json.loads(m["outcomePrices"]) if isinstance(m["outcomePrices"], str) else m["outcomePrices"]
            outcomes = json.loads(m["outcomes"])       if isinstance(m["outcomes"], str)       else m["outcomes"]
        except Exception:
            continue

        if not isinstance(prices, list) or len(prices) < 2:
            continue

        volume = float(m.get("volumeNum") or 0)
        if volume < MIN_VOLUME:
            skip_vol += 1
            continue

        best_bid   = float(m.get("bestBid")        or 0) or None
        best_ask   = float(m.get("bestAsk")        or 0) or None
        spread     = float(m.get("spread")         or 0) or None
        end_date   = m.get("endDate") or ""
        hrs        = hours_left(end_date)

        for i, raw_price in enumerate(prices):
            try:
                gp = float(raw_price)
            except Exception:
                continue

            if gp < MIN_PRICE or gp > MAX_PRICE:
                skip_price += 1
                continue

            fill   = best_ask if (best_ask and MIN_PRICE <= best_ask <= MAX_PRICE) else gp
            profit = round((1 / fill - 1) * 100, 2)

            if profit < MIN_PROFIT_PCT:
                skip_profit += 1
                continue

            outcome = outcomes[i] if i < len(outcomes) else "YES"
            key     = signal_key(m["question"], outcome)
            if key in sent_signals:
                continue

            score = conviction_score(fill, hrs, volume, profit)

            signals.append({
                "key":         key,
                "question":    m["question"],
                "outcome":     outcome,
                "fill_price":  fill,
                "profit":      profit,
                "best_bid":    best_bid,
                "best_ask":    best_ask,
                "spread":      spread,
                "has_clob":    bool(best_ask and best_bid),
                "category":    get_category(m["question"]),
                "volume":      volume,
                "end_date":    end_date,
                "hrs_left":    hrs,
                "slug":        m.get("slug", ""),
                "score":       score,
            })

    print(f"  [DEBUG] Skipped: vol={skip_vol} price={skip_price} profit={skip_profit} | Passed: {len(signals)}")
    signals.sort(key=lambda x: x["score"], reverse=True)
    return signals[:MAX_SIGNALS]

def probability_bar(price):
    filled = "█" * int(price * 10)
    empty  = "░" * (10 - int(price * 10))
    return f"{filled}{empty} {round(price*100)}%"

def build_message(signals):
    global scan_num
    now = datetime.now().strftime("%b %d · %H:%M")
    lines = [
        "🎯 *Polymarket Conviction Sniper*",
        f"📡 Scan #{scan_num} · {now}",
        "⚡ High-probability plays",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    for i, s in enumerate(signals, 1):
        hrs = s["hrs_left"]
        if hrs is None:
            urgency = "⚪ OPEN"
        elif hrs <= 6:
            urgency = "🔴 URGENT"
        elif hrs <= 24:
            urgency = "🟠 TODAY"
        else:
            urgency = "🟡 SOON"

        clob = " · `CLOB ✓`" if s["has_clob"] else ""

        lines += [
            "",
            f"*#{i} · {s['category']}{clob}*",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📌 _{s['question']}_",
            "",
            f"  {urgency} · `{fmt_expiry(hrs)}`",
            "",
            f"  📊 `{probability_bar(s['fill_price'])}`",
            f"  ✅ Outcome:  `{s['outcome']}`",
            f"  💰 Entry:    `{round(s['fill_price']*100)}¢`",
            f"  📈 Profit:   *+{s['profit']}%*",
            f"  🏆 Score:    `{s['score']}/100`",
        ]
        if s["has_clob"]:
            lines.append(f"  📖 Bid `{round(s['best_bid']*100)}¢` | Ask `{round(s['best_ask']*100)}¢`")
            if s["spread"]:
                lines.append(f"  Spread: `{round(s['spread'], 3)}`")
        lines += [
            "",
            f"  💵 Vol: `{fmt_volume(s['volume'])}`",
            f"  🔗 [Trade →](https://polymarket.com/event/{s['slug']})",
        ]

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"_Filters: {MIN_PRICE*100:.0f}–{MAX_PRICE*100:.0f}% prob · Min ${MIN_VOLUME:,} vol · +{MIN_PROFIT_PCT}% profit_",
        f"_Next scan in {SCAN_EVERY//60} min_",
    ]
    return "\n".join(lines)

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r   = requests.post(url, json={
        "chat_id":    CHAT_ID,
        "text":       text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }, timeout=10)
    r.raise_for_status()
    print(f"  [OK] Message sent to Telegram ✓")

def main():
    global scan_num, sent_signals
    print("=" * 55)
    print("  Polymarket Conviction Sniper — starting up")
    print(f"  Probability : {MIN_PRICE*100:.0f}% – {MAX_PRICE*100:.0f}%")
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
            print("  [INFO] Memory reset — fresh slate")

        try:
            print(f"\n[Scan #{scan_num}] Scanning...")
            signals = fetch_signals()
            print(f"[Scan #{scan_num}] {len(signals)} signals to send")

            if signals:
                msg = build_message(signals)
                send_telegram(msg)
                for s in signals:
                    sent_signals.add(s["key"])
            else:
                print(f"[Scan #{scan_num}] No signals passed filters")

        except Exception as e:
            print(f"[ERROR] {e}")

        print(f"Sleeping {SCAN_EVERY//60} min...\n")
        time.sleep(SCAN_EVERY)

if __name__ == "__main__":
    main()
