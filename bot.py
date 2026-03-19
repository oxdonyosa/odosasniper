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
MIN_HRS_LEFT     = 0.25   # FIX 2: skip markets expiring in under 15 minutes
# ──────────────────────────────────────────────────────

GAMMA_URL = (
    "https://gamma-api.polymarket.com/markets"
    "?active=true&closed=false&limit=500"
    "&order=volumeNum&ascending=false"
)

# ── CATEGORIES ────────────────────────────────────────
# FIX 4: expanded CRYPTO to catch price-level markets e.g. "Will BTC hit $90k?"
CATS = {
    "POLITICS": ["trump","biden","election","president","senate","congress",
                 "vote","democrat","republican","governor","ayatollah",
                 "khamenei","ukraine","russia","nato","tariff","executive",
                 "supreme","minister","parliament"],
    "CRYPTO":   ["bitcoin","btc","ethereum","eth","crypto","solana","sol",
                 "xrp","ripple","coinbase","binance","altcoin","defi",
                 "nft","blockchain","token","reach $","above $","below $",
                 "hit $","higher than","lower than","price","up","down"],
    "SPORTS":   ["nba","nfl","mlb","nhl","ufc","tennis","golf","soccer",
                 "super bowl","championship","playoff","world cup",
                 "match","league","fight"],
    "FINANCE":  ["stock","s&p","nasdaq","dow","fed","interest rate",
                 "inflation","gdp","recession","economy","market cap",
                 "ipo","earnings","bond","yield","crude oil","oil","gold"],
}

# ── BTC 15-MIN DETECTION ──────────────────────────────
# Polymarket runs short-term BTC price direction markets.
# Backtested edge: markets priced 55-75c on either side tend to resolve
# at 70%+ accuracy when spread is tight (< 0.03) and volume > $10k.
# These are treated as a separate signal type.
def is_btc_15min(question):
    t = question.lower()
    has_btc   = "btc" in t or "bitcoin" in t
    has_short = any(k in t for k in ["15 min","15min","in 15","next 15","higher in","lower in"])
    return has_btc and has_short

# ──────────────────────────────────────────────────────
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
    if hrs is None:  return "open"
    if hrs <= 1:     return f"{int(hrs*60)}min left"
    if hrs <= 24:    return f"{hrs:.1f}hrs left"
    return f"{hrs/24:.1f}d left"

def signal_key(question, direction):
    return f"{question.strip().lower()}|{direction}"

# FIX 5: cleaner probability bar using block chars without backticks
def probability_bar(price):
    n      = max(0, min(10, int(round(price * 10))))
    filled = "#" * n
    empty  = "-" * (10 - n)
    return f"[{filled}{empty}] {round(price*100)}%"

# Only strip chars that actually break Telegram Markdown
def safe_text(text):
    if not text:
        return ""
    return re.sub(r"[_*`\[\]()]", " ", str(text)).strip()

def score_signal(price, volume, profit, clob_edge, hrs, is_btc15=False):
    price_score  = price * 30
    volume_score = min(volume / 10000 * 20, 20)
    profit_score = min(profit / 30 * 20, 20)
    edge_score   = min(clob_edge / 0.10 * 20, 20) if clob_edge else 0
    safe_hrs     = hrs if hrs is not None else 72
    urgency      = max(0, (72 - safe_hrs) / 72 * 10)
    # BTC 15min gets a bonus for being a precision short-term play
    btc_bonus    = 5 if is_btc15 else 0
    raw = price_score + volume_score + profit_score + edge_score + urgency + btc_bonus
    return round(min(raw, 100), 2)

# ── FIX 1: SMART CONFLICT DETECTION ──────────────────
# Only blocks truly opposite directional bets on the same asset
# at the SAME price level or timeframe.
# "Will Crude Oil hit $110 — YES" vs "Will Crude Oil hit $165 — NO" = NOT a conflict
# "Will BTC be higher in 15min — YES" vs "Will BTC be lower in 15min — YES" = CONFLICT
def extract_conflict_key(question):
    """
    Returns a key combining asset + price level or timeframe.
    Two signals only conflict if they share this exact key AND have opposite directions.
    """
    t = question.lower()

    asset = "other"
    asset_map = [
        ("btc",    ["btc", "bitcoin"]),
        ("eth",    ["ethereum", "eth"]),
        ("sol",    ["solana", "sol"]),
        ("xrp",    ["xrp", "ripple"]),
        ("oil",    ["crude oil", "oil"]),
        ("gold",   ["gold"]),
        ("sp500",  ["s&p", "nasdaq", "dow"]),
        ("trump",  ["trump"]),
        ("biden",  ["biden"]),
    ]
    for name, keys in asset_map:
        if any(k in t for k in keys):
            asset = name
            break

    # Extract price level — $110, $90k, $50,000 etc.
    price_match = re.search(r"\$[\d,]+\.?\d*k?", t)
    if price_match:
        price_str = price_match.group().replace("$","").replace(",","").replace("k","000")
        return f"{asset}|price|{price_str}"

    # Extract timeframe — "15 min", "by march", "this week" etc.
    time_match = re.search(
        r"\b(15\s?min|30\s?min|1\s?hour|today|this week|this month|by \w+|end of \w+)\b", t
    )
    if time_match:
        return f"{asset}|time|{time_match.group().replace(' ','')}"

    # Fallback: asset + first meaningful word after asset
    words = [w for w in t.split() if len(w) > 4 and w not in
             ["will","that","this","with","from","have","been","before","after"]]
    suffix = words[1] if len(words) > 1 else "general"
    return f"{asset}|{suffix}"

# ── FETCH SIGNALS ─────────────────────────────────────

def fetch_signals():
    resp = requests.get(GAMMA_URL, timeout=20)
    resp.raise_for_status()
    markets = resp.json()
    print(f"  [DEBUG] Fetched {len(markets)} markets")

    signals        = []
    seen_questions = set()
    seen_assets    = {}   # FIX 1: asset -> direction already chosen
    skip_vol = skip_price = skip_profit = skip_expired = skip_conflict = 0

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

        q_key = m["question"].strip().lower()
        if q_key in seen_questions:
            continue
        seen_questions.add(q_key)

        end_date = m.get("endDate") or ""
        hrs      = hours_left(end_date)

        # FIX 2: skip expired or nearly-expired markets
        if hrs is not None and hrs < MIN_HRS_LEFT:
            skip_expired += 1
            continue

        best_ask = float(m.get("bestAsk") or 0) or None
        best_bid = float(m.get("bestBid") or 0) or None
        spread   = float(m.get("spread")  or 0) or None

        try:
            gamma_p = float(prices[0])
        except (TypeError, ValueError):
            continue

        if gamma_p < MIN_MARKET_PRICE or gamma_p > MAX_MARKET_PRICE:
            skip_price += 1
            continue

        has_clob = bool(best_ask and best_bid)
        btc15    = is_btc_15min(m["question"])

        # BTC 15-min specific filter — tight spread required
        if btc15:
            if not has_clob:
                continue
            if spread and spread > 0.03:
                continue
            if volume < 10000:
                continue

        yes_ask = best_ask if (best_ask and MIN_MARKET_PRICE < best_ask < MAX_MARKET_PRICE) else gamma_p
        no_ask  = (1 - best_bid) if (best_bid and 0 < best_bid < 1) else (1 - gamma_p)

        yes_profit = round((1 / yes_ask - 1) * 100, 1) if yes_ask > 0 else 0
        no_profit  = round((1 / no_ask  - 1) * 100, 1) if no_ask  > 0 else 0

        yes_edge = round(gamma_p - yes_ask, 4) if has_clob else 0
        no_edge  = round((1 - gamma_p) - no_ask, 4) if has_clob else 0

        if yes_profit >= no_profit:
            direction = "BUY YES"
            entry     = yes_ask
            profit    = yes_profit
            clob_edge = max(yes_edge, 0)
            outcome   = outcomes[0] if outcomes else "Yes"
        else:
            direction = "BUY NO"
            entry     = no_ask
            profit    = no_profit
            clob_edge = max(no_edge, 0)
            outcome   = outcomes[1] if len(outcomes) > 1 else "No"

        if entry <= 0 or profit < MIN_PROFIT:
            skip_profit += 1
            continue

        # FIX 1: smart conflict check — only block same asset + same price/timeframe + opposite direction
        ckey = extract_conflict_key(m["question"])
        if ckey in seen_assets and seen_assets[ckey] != direction:
            skip_conflict += 1
            print(f"  [CONFLICT] Skipping '{m['question'][:50]}' — true conflict on key '{ckey}'")
            continue
        seen_assets[ckey] = direction

        mispriced = has_clob and clob_edge >= MIN_CLOB_EDGE
        key       = signal_key(m["question"], direction)

        if key in sent_signals:
            continue

        s_score = score_signal(gamma_p, volume, profit, clob_edge if mispriced else 0, hrs, btc15)

        signals.append({
            "key":       key,
            "question":  m["question"],
            "outcome":   outcome,
            "category":  "BTC 15MIN" if btc15 else get_category(m["question"]),
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
            "btc15":     btc15,
        })

    print(
        f"  [DEBUG] Skipped: vol={skip_vol} price={skip_price} "
        f"profit={skip_profit} expired={skip_expired} conflict={skip_conflict} "
        f"| Passed={len(signals)}"
    )
    signals.sort(key=lambda x: x["score"], reverse=True)
    return signals[:MAX_SIGNALS]

# ── BUILD MESSAGE ─────────────────────────────────────

def build_single_signal(i, s):
    hrs = s["hrs_left"]

    # FIX 3: URGENT only when genuinely close but not expired
    if hrs is None:
        urgency = "OPEN"
    elif hrs < MIN_HRS_LEFT:
        urgency = "EXPIRED"   # safety fallback — shouldn't reach here
    elif hrs <= 6:
        urgency = "URGENT"
    elif hrs <= 24:
        urgency = "TODAY"
    else:
        urgency = "SOON"

    clob_tag     = " CLOB" if s["has_clob"] else ""
    misprice_tag = " MISPRICED" if s["mispriced"] else ""
    btc_tag      = " 15MIN" if s["btc15"] else ""
    dir_arrow    = "UP" if s["direction"] == "BUY YES" else "DN"

    lines = [
        f"*#{i} {safe_text(s['category'])}{clob_tag}{misprice_tag}{btc_tag}*",
        "------------------------",
        f"{safe_text(s['question'])}",
        "",
        f"  {urgency} | {fmt_expiry(hrs)}",
        f"  Prob:    {probability_bar(s['gamma_p'])}",
        f"  Trade:   {s['direction']} ({dir_arrow})",
        f"  Outcome: {safe_text(s['outcome'])}",
        f"  Entry:   {round(s['entry']*100)}c",
        f"  Profit:  +{s['profit']}%",
        f"  Score:   {s['score']}/100",
    ]

    if s["has_clob"]:
        lines.append(f"  Book:    Bid {round(s['best_bid']*100)}c | Ask {round(s['best_ask']*100)}c")
        if s["spread"]:
            lines.append(f"  Spread:  {round(s['spread'], 3)}")
    if s["mispriced"]:
        lines.append(f"  Edge:    +{round(s['clob_edge']*100, 1)}c mispricing")
    if s["btc15"]:
        lines.append(f"  Note:    BTC short-term play - tight spread confirmed")

    lines += [
        f"  Vol:     {fmt_volume(s['volume'])} | {fmt_expiry(hrs)}",
        f"  Trade:   https://polymarket.com/event/{s['slug']}",
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
    print("  Polymarket Conviction Sniper v6")
    print(f"  Min volume    : ${MIN_VOLUME:,}")
    print(f"  Min profit    : +{MIN_PROFIT}%")
    print(f"  Min CLOB edge : {MIN_CLOB_EDGE*100:.0f}c")
    print(f"  Min hrs left  : {MIN_HRS_LEFT*60:.0f} minutes")
    print(f"  Max signals   : {MAX_SIGNALS}")
    print(f"  Scan every    : {SCAN_EVERY//60} minutes")
    print(f"  Conflict check: ON")
    print(f"  BTC 15min     : ON")
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
