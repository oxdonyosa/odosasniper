# Polymarket Conviction Sniper
### High Probability Compounding Edition

---

Built for one thing — finding the highest conviction plays on Polymarket and sending them straight to Telegram. Not chasing moonshots. Not spamming signals. Just clean, filtered, high-probability trades you can compound into consistent growth.

---

## The Strategy

Most Polymarket bots hunt cheap markets with massive upside. The problem is cheap markets lose most of the time. This bot does the opposite.

It only surfaces outcomes already priced at **80c–94c** by the market — meaning the crowd already agrees there is an 80–94% chance of this resolving YES or NO. The profit per trade looks small (+6% to +25%) but the win rate is genuinely high. Compound that consistently and it compounds fast.

The edge is not prediction. The edge is discipline — only taking trades where the crowd is already highly confident, the order book is liquid, and the market resolves within the week.

---

## Filters

Every signal must pass all of these before it gets sent:

| Filter | Value | Why |
|--------|-------|-----|
| Probability range | 80%–94% | High conviction only |
| Min volume | $10,000 | Strong market consensus |
| Min liquidity | $2,000 | Must be actually fillable |
| Max expiry | 7 days | No long exposure |
| CLOB required | Yes | Real order book must exist |
| Max signals/day | 2 | Quality over quantity |
| Scan interval | Every 12 hours | No spam |

---

## What a Signal Looks Like

```
Polymarket Conviction Play
Signal 1 of 2 today

Will the Fed hold rates at the March meeting?

  Outcome:    BUY YES
  Prob:       [########--] 84%
  Entry:      84c
  To win:     100c
  Profit:     +19.0%
  Conviction: 91/100

  Book:  Bid 83c | Ask 84c
  Spread: 0.010
  Vol:   $48K
  Exp:   1.5d left

  Trade: https://polymarket.com/event/...

Strategy: high probability compounding
Only bet what fits your bankroll sizing
```

---

## Setup

### Step 1 — Create your Telegram bot
1. Open Telegram → search **@BotFather** → send `/newbot`
2. Follow the prompts → copy the token it gives you

### Step 2 — Get your Chat ID
- **Personal**: search **@userinfobot** → send `/start` → copy your numeric ID
- **Channel**: add your bot as admin → forward a message from the channel to **@userinfobot** → copy the ID starting with `-100`

### Step 3 — Deploy on Railway
1. Upload `bot.py` and `requirements.txt` to a GitHub repo
2. Go to **railway.app** → New Project → Deploy from GitHub
3. Add these environment variables:

| Variable | Value |
|----------|-------|
| `BOT_TOKEN` | your BotFather token |
| `CHAT_ID` | your Telegram ID or channel ID |
| `SCAN_EVERY` | `43200` (12 hours) |
| `MAX_DAILY` | `2` |

4. Deploy — Railway runs it 24/7

---

## Compounding Logic

The bot does not size your bets. That is your job. A simple approach that works:

- Start with a fixed unit (e.g. $20 per trade)
- After each win, increase unit by 10%
- After each loss, return to base unit
- Never bet more than 5% of total bankroll on a single trade

At 80% win rate and +15% average profit per trade, $100 becomes ~$340 in 30 trades.

---

## Changelog

**v7 — High Probability Edition (current)**
- Complete strategy rebuild — 80c+ probability filter only
- CLOB order book required for every signal
- Max 2 signals per day, individually delivered
- Daily counter with midnight UTC reset
- Weekly memory reset so resolved markets refresh
- Conviction score based on price certainty + spread tightness + volume
- Removed all low-probability, high-upside noise

**v6 — Smart Conflict Detection**
- Conflict detection upgraded to use asset + price level key
- BTC 15min short-term markets added as separate signal type
- Score capped at 100

**v5 — Bug Fix Patch**
- BUY NO entry price corrected to use 1 - best_bid
- BUY NO outcome label fixed
- safe_text regex cleaned up
- Expired markets filtered out

**v1–v4 — Initial builds**
- Gamma + CLOB dual API integration
- Deduplication and category filtering
- Telegram message splitting for long outputs
