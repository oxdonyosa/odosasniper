# Polymarket Sniper Bot — Telegram Setup Guide

Scans Polymarket every 30 minutes using the Gamma + CLOB APIs
and sends high-profit signals directly to your Telegram chat or channel.

---

## STEP 1 — Create your Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Give it a name e.g. `Polymarket Sniper`
4. Give it a username e.g. `polymarket_sniper_yosa_bot`
5. BotFather will give you a **token** like:
   `7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`
6. Save that token — you'll need it

---

## STEP 2 — Get your Chat ID

**Option A — Personal (signals only go to you)**
1. Search for **@userinfobot** on Telegram
2. Send `/start`
3. It will reply with your numeric ID e.g. `123456789`

**Option B — Channel (signals go to a public/private channel)**
1. Create a channel in Telegram
2. Add your bot as an **Administrator** of the channel
3. Forward any message from the channel to **@userinfobot**
4. It will show the channel ID (starts with `-100...`)

---

## STEP 3 — Configure the bot

Open `bot.py` and set your values at the top:

```python
BOT_TOKEN   = "YOUR_BOT_TOKEN_HERE"     # from BotFather
CHAT_ID     = "YOUR_CHAT_ID_HERE"       # your personal ID or channel ID
MIN_PROFIT  = 15.0                       # minimum % profit to alert on
SCAN_EVERY  = 1800                       # seconds between scans (1800 = 30 min)
MAX_SIGNALS = 5                          # max alerts per scan
```

Or use environment variables (recommended for deployment):
```
BOT_TOKEN=your_token
CHAT_ID=your_chat_id
MIN_PROFIT=15
SCAN_EVERY=1800
MAX_SIGNALS=5
```

---

## STEP 4 — Run the bot

### Locally (your PC / Mac)
```bash
pip install -r requirements.txt
python bot.py
```

### Free cloud hosting on Railway
1. Go to https://railway.app and sign up (free)
2. Click **New Project → Deploy from GitHub**
3. Upload or push this folder to a GitHub repo
4. Add environment variables in Railway dashboard:
   - `BOT_TOKEN`
   - `CHAT_ID`
   - `MIN_PROFIT`
5. Deploy — Railway runs it 24/7 for free

### Free cloud hosting on Render
1. Go to https://render.com and sign up
2. New → **Background Worker**
3. Connect your GitHub repo
4. Set **Build Command**: `pip install -r requirements.txt`
5. Set **Start Command**: `python bot.py`
6. Add environment variables and deploy

---

## What the signals look like

```
🎯 Polymarket Sniper Bot
📡 Scan #1 · Mar 16 · 14:32
━━━━━━━━━━━━━━━━━━━━━━

🟡 #1 · 🏛 POLITICS · CLOB LIVE
━━━━━━━━━━━━━━━━━━━━━━
📌 Will Trump say "Ayatollah" or "Khamenei" this week?

  • Outcome: NO
  • Gamma price: 84¢ → +19.0%
  • CLOB best ask: 83¢ → +20.5%
  • Order book: Bid 82¢ | Ask 83¢
  • Spread: 0.010
  • Volume: $12K · Expires: 2d left
  • Trade on Polymarket
```

---

## Tuning tips

| Setting | Conservative | Aggressive |
|---------|-------------|-----------|
| MIN_PROFIT | 30% | 10% |
| SCAN_EVERY | 3600 (1hr) | 900 (15min) |
| MAX_SIGNALS | 3 | 10 |

Higher `MIN_PROFIT` = fewer but stronger signals.
Lower `SCAN_EVERY` = more frequent scanning.
