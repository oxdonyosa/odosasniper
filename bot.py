import os
import time
import json
import re
import requests
from datetime import datetime, timezone

# ── CONFIG ────────────────────────────────────────────
BOT_TOKEN      = os.environ.get("BOT_TOKEN",      "YOUR_BOT_TOKEN_HERE")
CHAT_ID        = os.environ.get("CHAT_ID",        "YOUR_CHAT_ID_HERE")
SCAN_EVERY     = int(os.environ.get("SCAN_EVERY", "7200"))  # 2 hours
MAX_SIGNALS    = int(os.environ.get("MAX_SIGNALS","5"))

# ── FILTERS ───────────────────────────────────────────
MIN_VOLUME       = 3000   # minimum $3k volume
MIN_MARKET_PRICE = 0.05   # ignore near-zero junk
MAX_MARKET_PRICE = 0.95   # ignore near-certain markets
MIN_PROFIT       = 10.0   # minimum % profit
MIN_CLOB_EDGE    = 0.02   # minimum CLOB mispricing gap
# ──────────────────────────────────────────────────────

GAMMA_URL = (
    "https://gamma-api.polymarket.com/markets"
    "?active=true&closed=false&limit=500"
    "&order=volumeNum&ascending=false"
)

CATS = {
    "POLITICS": ["trump","biden","election","president","senate","congress",
                 "vote","democrat","republican","governor","ayatollah",
                 "khamenei","ukraine","russia","nato","tariff","executive",
                 "supreme","minister","parliament"],
    "CRYPTO":   ["bitcoin","btc","ethereum","eth","crypto","solana","sol",
                 "xrp","ripple","coinbase","binance","altcoin","defi",
                 "nft","blockchain","token"],
    "SPORTS":   ["nba","nfl","mlb","nhl","ufc","tennis","golf","soccer",
                 "super bowl","championship","playoff","world cup",
                 "match","league","fight"],
    "FINANCE":  ["stock","s&p","nasdaq","dow","fed","interest rate",
                 "inflation","gdp","recession","economy","market cap",
                 "ipo","earnings","bond","yield"],
}

sent_signals = set()
scan_num     = 0

# ── HELPERS ───────────────────────────────────────────

def get_category(question):
    t = question.lower()
    for cat, keywords in CATS.items():
        if any(k in t for k in keywords):
            return cat
    return "OTHER"

def fmt_volume(v):
    if not v:    return "$0"
    if v >= 1e6: return f"${v/1e6:.1f}M"
    if v >= 1e3: return f"${v/1e3:.0f}K"
    return f"${v:.0f}"

def hours_left(end_date):
    if not end_date:
        return None
    try:
        dt  = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return max((dt - now).total_seconds() / 3600, 0)
    except Exception:
        return None

def fmt_expiry(hrs):
    if hrs is None: return "open"
    if hrs <= 1:    return f"{int(hrs*60)}min left"
    if hrs <= 24:   return f"{hrs:.1f}hrs left"
    return f"{hrs/24:.1f}d left"

def signal_key(question, direction):
    return f"{question.strip().lower()}|{direction}"

def probability_bar(price):
    n      = max(0, min(10, int(round(price * 10))))
    filled = "X" * n
    empty  = "." * (10 - n)
    return f"[{filled}{empty}] {round(price*100)}%"

# FIX 2: safe_text strips ALL markdown special chars to prevent Telegram parse errors
def safe_text(text):
    if not text:
        return ""
    return re.sub(r"[_*`\[\]()#]", " ", str(text)).strip()

def score_signal(price, volume, profit, clob_edge, hrs):
    price_score  = price * 30
    volume_score = min(volume / 10000 * 20, 20)
    profit_score = min(profit / 30 * 20, 20)
    edge_score   = min(clob_edge / 0.10 * 20, 20) if clob_edge else 0
    # FIX 5: documented — no end date = 0 urgency (neutral, not penalised)
    safe_hrs     = hrs if hrs is not None else 72
    urgency      = max(0, (72 - safe_hrs) / 72 * 10)
    return round(price_score + volume_score + profit_score + edge_score + urgency, 2)

# ── FETCH SIGNALS ─────────────────────────────────────

def fetch_signals():
    resp = requests.get(GAMMA_URL, timeout=20)
    resp.raise_for_status()
    markets = resp.json()
    print(f"  [DEBUG] Fetched {len(markets)} markets")

    signals        = []
    seen_questions = set()
    skip_vol = skip_price = skip_profit = 0

    for m in markets:
        if not m.get("question") or not m.get("outcomePrices") or not m.get("outcomes"):
            continue

        # FIX 4: wrapped price parsing in try/except
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

        q_key = m["question"].strip().lower()
        if q_key in seen_questions:
            continue
        seen_questions.add(q_key)

        best_ask = float(m.get("bestAsk") or 0) or None
        best_bid = float(m.get("bestBid") or 0) or None
        spread   = float(m.get("spread")  or 0) or None
        end_date = m.get("endDate") or ""
        hrs      = hours_left(end_date)

        # FIX 4: safe price parse for prices[0]
        try:
            gamma_p = float(prices[0])
        except (TypeError, ValueError):
            continue

        if gamma_p < MIN_MARKET_PRICE or gamma_p > MAX_MARKET_PRICE:
            skip_price += 1
            continue

        has_clob = bool(best_ask and best_bid)

        # Determine direction and entry price
        # YES ask = best_ask (cost to buy YES)
        # NO ask  = 1 - best_bid (cost to buy NO = complement of YES bid)
        yes_ask = best_ask if (best_ask and MIN_MARKET_PRICE < best_ask < MAX_MARKET_PRICE) else gamma_p
        # FIX 3: NO entry uses 1 - best_bid (correct NO ask price), not 1 - best_ask
        no_ask  = (1 - best_bid) if (best_bid and 0 < best_bid < 1) else (1 - gamma_p)

        yes_profit = round((1 / yes_ask - 1) * 100, 1) if yes_ask > 0 else 0
        no_profit  = round((1 / no_ask  - 1) * 100, 1) if no_ask  > 0 else 0

        # CLOB edge: how far is the ask from the gamma mid
        yes_edge = round(gamma_p - yes_ask, 4) if has_clob else 0
        no_edge  = round((1 - gamma_p) - no_ask, 4) if has_clob else 0  # positive = NO is cheap

        # Pick better direction
        if yes_profit >= no_profit:
            direction = "BUY YES"
            entry     = yes_ask
            profit    = yes_profit
            clob_edge = max(yes_edge, 0)
            # FIX 1: outcome label matches direction
            outcome   = outcomes[0] if outcomes else "Yes"
        else:
            direction = "BUY NO"
            entry     = no_ask
            profit    = no_profit
            clob_edge = max(no_edge, 0)
            # FIX 1: outcome label matches direction — use outcomes[1] for NO
            outcome   = outcomes[1] if len(outcomes) > 1 else "No"

        if entry <= 0 or profit < MIN_PROFIT:
            skip_profit += 1
            continue

        mispriced = has_clob and clob_edge >= MIN_CLOB_EDGE
        key       = signal_key(m["question"], direction)

        if key in sent_signals:
            continue

        s_score = score_signal(gamma_p, volume, profit, clob_edge if mispriced else 0, hrs)

        signals.append({
            "key":       key,
            "question":  m["question"],
            "outcome":   outcome,
            "category":  get_category(m["question"]),
            "gamma_p":   gamma_p,
            "entry":     round(entry, 3),
            "best_bid":  best_bid,
            "best_ask":  best_ask,
            "spread":    spread,
            "has_clob":  has_clob,
            "clob_edge": clob_edge,
            "mispriced": mispriced,
            "direction": direction,
            "profit":    profit,
            "volume":    volume,
            "hrs_left":  hrs,
            "slug":      m.get("slug", ""),
            "score":     s_score,
        })

    print(f"  [DEBUG] Skipped vol={skip_vol} price={skip_price} profit={skip_profit} | Passed={len(signals)}")
    signals.sort(key=lambda x: x["score"], reverse=True)
    return signals[:MAX_SIGNALS]

# ── BUILD MESSAGE ─────────────────────────────────────

def build_single_signal(i, s):
    hrs  = s["hrs_left"]

    if hrs is None:
        urgency = "OPEN"
    elif hrs <= 6:
        urgency = "URGENT"
    elif hrs <= 24:
        urgency = "TODAY"
    else:
        urgency = "SOON"

    # FIX 2: no backticks inside bold — plain text tags only
    clob_tag      = " CLOB" if s["has_clob"] else ""
    misprice_tag  = " MISPRICED" if s["mispriced"] else ""
    dir_arrow     = "UP" if s["direction"] == "BUY YES" else "DN"

    lines = [
        f"*#{i} {safe_text(s['category'])}{clob_tag}{misprice_tag}*",
        "------------------------",
        f"{safe_text(s['question'])}",
        "",
        f"  {urgency} | {fmt_expiry(hrs)}",
        f"  Prob:   {probability_bar(s['gamma_p'])}",
        f"  Trade:  {s['direction']} ({dir_arrow})",
        f"  Outcome: {safe_text(s['outcome'])}",
        f"  Entry:  {round(s['entry']*100)}c",
        f"  Profit: +{s['profit']}%",
        f"  Score:  {s['score']}/100",
    ]

    if s["has_clob"]:
        lines.append(f"  Book: Bid {round(s['best_bid']*100)}c | Ask {round(s['best_ask']*100)}c")
        if s["spread"]:
            lines.append(f"  Spread: {round(s['spread'], 3)}")
    if s["mispriced"]:
        lines.append(f"  Edge: +{round(s['clob_edge']*100, 1)}c mispricing")

    lines += [
        f"  Vol: {fmt_volume(s['volume'])} | {fmt_expiry(hrs)}",
        f"  Trade: https://polymarket.com/event/{s['slug']}",
        "",
    ]
    return "\n".join(lines)

def build_messages(signals):
    global scan_num
    now = datetime.now().strftime("%b %d %H:%M")

    header = (
        f"*Polymarket Conviction Sniper*\n"
        f"Scan {scan_num} | {now}\n"
        f"Top {len(signals)} plays\n"
        f"------------------------\n\n"
    )
    footer = (
        f"------------------------\n"
        f"Min profit +{MIN_PROFIT}% | Min vol ${MIN_VOLUME:,}\n"
        f"Next scan in {SCAN_EVERY//60} min"
    )

    messages = []
    current  = header

    for i, s in enumerate(signals, 1):
        block = build_single_signal(i, s)
        if len(current) + len(block) > 3800:
            current += footer
            messages.append(current)
            current = f"*Sniper continued (scan {scan_num})*\n\n"
        current += block

    current += footer
    messages.append(current)
    return messages

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
    global scan_num, sent_signals
    print("=" * 55)
    print("  Polymarket Conviction Sniper")
    print(f"  Min volume   : ${MIN_VOLUME:,}")
    print(f"  Min profit   : +{MIN_PROFIT}%")
    print(f"  Min CLOB edge: {MIN_CLOB_EDGE*100:.0f}c")
    print(f"  Max signals  : {MAX_SIGNALS}")
    print(f"  Scan every   : {SCAN_EVERY//60} minutes")
    print("=" * 55)

    last_reset = time.time()

    while True:
        scan_num += 1

        if time.time() - last_reset > 86400:
            sent_signals.clear()
            last_reset = time.time()
            print("  [INFO] Memory reset")

        try:
            print(f"\n[Scan #{scan_num}] Scanning...")
            signals = fetch_signals()
            print(f"[Scan #{scan_num}] {len(signals)} signals found")

            if signals:
                messages = build_messages(signals)
                for idx, msg in enumerate(messages):
                    send_telegram(msg)
                    print(f"  [OK] Message {idx+1}/{len(messages)} sent")
                    if len(messages) > 1:
                        time.sleep(1)
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
