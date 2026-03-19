import os
import time
import json
import re
import requests
from datetime import datetime, timezone

# ── CONFIG ────────────────────────────────────────────
BOT_TOKEN  = os.environ.get("BOT_TOKEN",  "YOUR_BOT_TOKEN_HERE")
CHAT_ID    = os.environ.get("CHAT_ID",    "YOUR_CHAT_ID_HERE")
SCAN_EVERY = int(os.environ.get("SCAN_EVERY", "43200"))  # every 12 hours
MAX_DAILY  = int(os.environ.get("MAX_DAILY", "2"))        # max signals per day

# ── STRICT HIGH PROBABILITY FILTERS ──────────────────
MIN_PRICE    = 0.80   # must be priced 80c or higher (80%+ probability)
MAX_PRICE    = 0.94   # cap at 94c — above this the edge is too thin
MIN_VOLUME   = 10000  # minimum $10k volume — ensures strong market consensus
MAX_DAYS     = 7      # must resolve within 7 days — no long exposure
MIN_LIQUIDITY= 2000   # minimum $2k liquidity — must be fillable
# ──────────────────────────────────────────────────────

GAMMA_URL = (
    "https://gamma-api.polymarket.com/markets"
    "?active=true&closed=false&limit=500"
    "&order=volumeNum&ascending=false"
)

sent_signals  = set()
daily_count   = 0
last_day      = None
scan_num      = 0

# ── HELPERS ───────────────────────────────────────────

def safe_text(text):
    if not text: return ""
    return re.sub(r"[_*`\[\]()]", " ", str(text)).strip()

def fmt_volume(v):
    if not v:    return "$0"
    if v >= 1e6: return f"${v/1e6:.1f}M"
    if v >= 1e3: return f"${v/1e3:.0f}K"
    return f"${v:.0f}"

def hours_left(end_date):
    if not end_date: return None
    try:
        dt  = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return max((dt - now).total_seconds() / 3600, 0)
    except Exception:
        return None

def fmt_expiry(hrs):
    if hrs is None: return "—"
    if hrs <= 1:    return f"{int(hrs*60)}min left"
    if hrs <= 24:   return f"{hrs:.1f}hrs left"
    return f"{hrs/24:.1f}d left"

def probability_bar(price):
    n = max(0, min(10, int(round(price * 10))))
    return f"[{'#'*n}{'-'*(10-n)}] {round(price*100)}%"

def get_today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

# ── FETCH SIGNALS ─────────────────────────────────────

def fetch_signals():
    resp = requests.get(GAMMA_URL, timeout=20)
    resp.raise_for_status()
    markets = resp.json()
    print(f"  [DEBUG] Fetched {len(markets)} markets")

    candidates       = []
    seen_questions   = set()
    skip_vol = skip_price = skip_days = skip_liq = skip_clob = 0

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

        # Volume filter
        volume = float(m.get("volumeNum") or 0)
        if volume < MIN_VOLUME:
            skip_vol += 1
            continue

        # Liquidity filter
        liquidity = float(m.get("liquidityNum") or 0)
        if liquidity < MIN_LIQUIDITY:
            skip_liq += 1
            continue

        # Dedup
        q_key = m["question"].strip().lower()
        if q_key in seen_questions:
            continue
        seen_questions.add(q_key)

        # Expiry filter — must resolve within MAX_DAYS
        end_date = m.get("endDate") or ""
        hrs      = hours_left(end_date)
        if hrs is not None and (hrs < 0.5 or hrs > MAX_DAYS * 24):
            skip_days += 1
            continue

        best_ask = float(m.get("bestAsk") or 0) or None
        best_bid = float(m.get("bestBid") or 0) or None
        spread   = float(m.get("spread")  or 0) or None

        # CLOB required — we need real order book to confirm price
        if not best_ask or not best_bid:
            skip_clob += 1
            continue

        # Check each outcome for high probability
        for i, raw_price in enumerate(prices):
            try:
                gp = float(raw_price)
            except (TypeError, ValueError):
                continue

            # Only high probability outcomes
            if gp < MIN_PRICE or gp > MAX_PRICE:
                skip_price += 1
                continue

            # Use CLOB ask as real entry price
            # For YES: entry = best_ask
            # Only accept if CLOB ask is also in the high-prob range
            if i == 0:  # YES side
                entry = best_ask if (MIN_PRICE <= best_ask <= MAX_PRICE) else gp
                direction = "YES"
                outcome   = outcomes[0] if outcomes else "Yes"
            else:       # NO side — entry = 1 - best_bid
                entry = (1 - best_bid) if (0 < best_bid < 1) else (1 - gp)
                direction = "NO"
                outcome   = outcomes[i] if i < len(outcomes) else "No"

            if entry <= 0 or entry < MIN_PRICE or entry > MAX_PRICE:
                continue

            profit  = round((1 / entry - 1) * 100, 1)
            # Spread tightness — tighter spread = more confidence in price
            spread_score = max(0, 1 - (spread or 0.05) / 0.05)
            # Volume score
            vol_score    = min(volume / 50000, 1)
            # Conviction: highest price + tightest spread + most volume
            conviction   = round((entry * 50) + (spread_score * 30) + (vol_score * 20), 2)
            conviction   = min(conviction, 100)

            key = f"{m['question'].strip().lower()}|{direction}"
            if key in sent_signals:
                continue

            candidates.append({
                "key":        key,
                "question":   m["question"],
                "outcome":    outcome,
                "direction":  direction,
                "entry":      round(entry, 3),
                "gamma_p":    gp,
                "best_bid":   best_bid,
                "best_ask":   best_ask,
                "spread":     spread,
                "profit":     profit,
                "volume":     volume,
                "liquidity":  liquidity,
                "hrs_left":   hrs,
                "slug":       m.get("slug", ""),
                "conviction": conviction,
            })

    print(
        f"  [DEBUG] Skipped: vol={skip_vol} price={skip_price} "
        f"days={skip_days} liq={skip_liq} clob={skip_clob} "
        f"| Candidates={len(candidates)}"
    )

    # Sort by conviction — most certain plays first
    candidates.sort(key=lambda x: x["conviction"], reverse=True)
    return candidates

# ── BUILD MESSAGE ─────────────────────────────────────

def build_message(s, rank, total_today):
    hrs     = s["hrs_left"]
    profit  = s["profit"]
    implied = round(s["entry"] * 100)

    lines = [
        f"*Polymarket Conviction Play*",
        f"Signal {rank} of {MAX_DAILY} today",
        f"",
        f"*{safe_text(s['question'])}*",
        f"",
        f"  Outcome:    BUY {s['direction']}",
        f"  Prob:       {probability_bar(s['entry'])}",
        f"  Entry:      {implied}c",
        f"  To win:     100c",
        f"  Profit:     +{profit}%",
        f"  Conviction: {s['conviction']}/100",
        f"",
        f"  Book:  Bid {round(s['best_bid']*100)}c | Ask {round(s['best_ask']*100)}c",
    ]
    if s["spread"]:
        lines.append(f"  Spread: {round(s['spread'], 3)}")
    lines += [
        f"  Vol:   {fmt_volume(s['volume'])}",
        f"  Exp:   {fmt_expiry(hrs)}",
        f"",
        f"  Trade: https://polymarket.com/event/{s['slug']}",
        f"",
        f"------------------------",
        f"Strategy: high probability compounding",
        f"Only bet what fits your bankroll sizing",
    ]
    return "\n".join(lines)

# ── TELEGRAM ──────────────────────────────────────────

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r   = requests.post(url, json={
        "chat_id":    CHAT_ID,
        "text":       text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }, timeout=10)
    r.raise_for_status()

# ── MAIN LOOP ─────────────────────────────────────────

def main():
    global scan_num, daily_count, last_day, sent_signals

    print("=" * 55)
    print("  Polymarket Conviction Sniper — High Prob Mode")
    print(f"  Min probability : {MIN_PRICE*100:.0f}%–{MAX_PRICE*100:.0f}%")
    print(f"  Min volume      : ${MIN_VOLUME:,}")
    print(f"  Min liquidity   : ${MIN_LIQUIDITY:,}")
    print(f"  Max expiry      : {MAX_DAYS} days")
    print(f"  Max signals/day : {MAX_DAILY}")
    print(f"  Scan every      : {SCAN_EVERY//3600:.0f} hours")
    print("=" * 55)

    mem_reset = time.time()

    while True:
        scan_num += 1
        today = get_today()

        # Reset daily counter on new day
        if last_day != today:
            daily_count = 0
            last_day    = today
            print(f"  [INFO] New day — daily counter reset")

        # Reset full memory weekly
        if time.time() - mem_reset > 604800:
            sent_signals.clear()
            mem_reset = time.time()
            print(f"  [INFO] Weekly memory reset")

        if daily_count >= MAX_DAILY:
            print(f"[Scan #{scan_num}] Daily limit reached ({MAX_DAILY}) — sleeping until next scan")
            time.sleep(SCAN_EVERY)
            continue

        try:
            print(f"\n[Scan #{scan_num}] Scanning... ({daily_count}/{MAX_DAILY} sent today)")
            candidates = fetch_signals()

            slots_left = MAX_DAILY - daily_count
            top        = candidates[:slots_left]

            if top:
                for s in top:
                    daily_count += 1
                    msg = build_message(s, daily_count, MAX_DAILY)
                    send_telegram(msg)
                    sent_signals.add(s["key"])
                    print(f"  [OK] Signal {daily_count}/{MAX_DAILY} sent: {s['question'][:50]}")
                    print(f"       Entry={round(s['entry']*100)}c Profit=+{s['profit']}% Conv={s['conviction']}")
                    if len(top) > 1:
                        time.sleep(2)
            else:
                print(f"[Scan #{scan_num}] No signals passed filters today")

        except Exception as e:
            print(f"[ERROR] {e}")

        print(f"Sleeping {SCAN_EVERY//3600:.0f}hrs...\n")
        time.sleep(SCAN_EVERY)

if __name__ == "__main__":
    main()
