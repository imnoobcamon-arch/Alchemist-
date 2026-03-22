# Alchemist-
Telegram bot for forex charts technical analysis based on ICT concepts. On EURUSD 
# 🤖 ICT Forex Analysis Bot — EURUSD

A professional, modular Python bot that analyses **EURUSD** using pure **ICT (Inner Circle Trader)** concepts — no lagging indicators (no RSI, MACD, Bollinger Bands).

---

## 📂 Project Structure

```
forex_ict_bot/
│── main.py                  # Entry point (CLI)
│── config.py                # All settings in one place
│── requirements.txt
│── README.md
│
├── data/
│   └── fetcher.py           # yfinance / MT5 price data layer
│
├── strategies/
│   └── ict_strategy.py      # Core ICT entry model (strict checklist)
│
├── utils/
│   ├── market_structure.py  # Swing H/L, BOS, MSS, bias, premium/discount
│   ├── liquidity.py         # Equal H/L pools, sweeps, inducement, PDH/PDL
│   ├── fvg.py               # Fair Value Gap / imbalance detection
│   ├── order_blocks.py      # Order Blocks & Breaker Blocks
│   ├── sessions.py          # Kill zones, session ranges, Asian range
│   └── logger.py            # Rotating log file + console output
│
├── signals/
│   └── signal_generator.py  # Format, save (CSV + JSON), Telegram
│
├── backtest/
│   └── backtester.py        # Walk-forward backtest engine
│
├── logs/                    # Auto-created — rotating log files
└── journal/                 # Auto-created — trade_journal.csv
```

---

## 🎯 ICT Concepts Implemented

| Concept | Location |
|---|---|
| Swing High / Swing Low | `utils/market_structure.py` |
| Break of Structure (BOS) | `utils/market_structure.py` |
| Market Structure Shift (MSS) | `utils/market_structure.py` |
| Premium & Discount zones | `utils/market_structure.py` |
| Equal Highs / Equal Lows (BSL/SSL) | `utils/liquidity.py` |
| Liquidity Sweeps (stop hunts) | `utils/liquidity.py` |
| Inducement | `utils/liquidity.py` |
| Previous Day High / Low | `utils/liquidity.py` |
| Fair Value Gaps (FVG) | `utils/fvg.py` |
| Consequent Encroachment (CE) | `utils/fvg.py` |
| Order Blocks (OB) | `utils/order_blocks.py` |
| Breaker Blocks | `utils/order_blocks.py` |
| Kill Zones (London / NY) | `utils/sessions.py` |
| Asian Range | `utils/sessions.py` |
| 5M Entry Confirmation | `strategies/ict_strategy.py` |
| Multi-Timeframe Analysis | `strategies/ict_strategy.py` |

---

## ⚙️ Setup Guide

### 1. Prerequisites

- Python 3.9 or higher
- pip (comes with Python)

### 2. Clone / Download

```bash
git clone https://github.com/YOUR_USERNAME/forex_ict_bot.git
cd forex_ict_bot
```

### 3. Create a virtual environment (recommended)

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

### 5. Configure the bot

Open `config.py` and review the settings:

```python
# Data source: "yfinance" (free, default) or "mt5" (requires MT5 on Windows)
DATA_SOURCE = "yfinance"

# Risk settings
DEFAULT_RISK_PERCENT    = 1.0     # % of account per trade
DEFAULT_ACCOUNT_BALANCE = 10_000  # USD

# Optional Telegram alerts
TELEGRAM_ENABLED = False
TELEGRAM_TOKEN   = "YOUR_BOT_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"
```

---

## 🚀 Running the Bot

### Single analysis pass (most common)
```bash
python main.py
```

### Demo mode — see signal output without live data
```bash
python main.py --demo
```

### Live loop — analyse every 5 minutes
```bash
python main.py --loop
python main.py --loop --interval 15   # every 15 minutes
```

### Walk-forward backtest
```bash
python main.py --backtest
```

---

## 📊 Example Signal Output

```
╔══════════════════════════════════════════╗
║  📈  ICT TRADE SIGNAL — EURUSD          ║
╠══════════════════════════════════════════╣
║  Direction    : BUY                     ║
║  Entry Price  : 1.08450                 ║
║  Stop Loss    : 1.08120                 ║
║  Take Profit  : 1.09110                 ║
║  Risk : Reward: 1 : 2.0                 ║
║  Risk Pips    : 33.0                    ║
║  Reward Pips  : 66.0                    ║
║  Lot Size     : 0.30                    ║
╠══════════════════════════════════════════╣
║  Confidence   : 🟢 High                 ║
║  Session      : London                  ║
║  Kill Zone    : London_Open             ║
║  Time (UTC+3) : 2024-11-01 10:30 UTC+3  ║
╠══════════════════════════════════════════╣
║  ── ICT Analysis ──                     ║
║  HTF Bias     : BULLISH                 ║
║  Liq Sweep    : SSL sweep at 1.08120    ║
║  Structure    : Bullish MSS confirmed   ║
║  POI          : Bullish OB+FVG conflu.  ║
║  Prev Day H   : 1.08850                 ║
║  Prev Day L   : 1.07990                 ║
║  Asian Hi     : 1.08610                 ║
║  Asian Lo     : 1.08310                 ║
╚══════════════════════════════════════════╝

📝 REASONING
─────────────────────────────────────────────────────────────
D1 is bullish → looking for BUYs. Sell-side liquidity swept
at 1.08120 during Asian session, closed back above. Bullish
MSS confirmed on H1 at 1.08430. Price retesting bullish OB
confluence with H1 FVG. 5M bullish engulfing candle during
London Open Kill Zone.
─────────────────────────────────────────────────────────────
```

---

## 🧠 Entry Model (Strict Checklist)

The bot only generates a signal when **all** conditions are met:

```
✅ 1. D1 bias determined (bullish / bearish)
✅ 2. Price in correct P/D zone (discount for buys, premium for sells)
✅ 3. Liquidity sweep confirmed on H1 (SSL for buys / BSL for sells)
✅ 4. MSS or BOS confirmed on H1 in bias direction
✅ 5. Valid POI identified (Order Block or FVG, or confluence of both)
✅ 6. Kill Zone active (London Open or New York Open)
✅ 7. 5M confirmation candle (engulfing or strong directional)
✅ 8. Risk:Reward ≥ 2.0 (configurable)
```

---

## ⏰ Session Schedule (UTC+3)

| Session | Time (UTC+3) |
|---|---|
| Asian | 02:00 – 09:00 |
| London | 10:00 – 18:00 |
| New York | 15:00 – 23:00 |

| Kill Zone | Time (UTC+3) |
|---|---|
| London Open | 10:00 – 12:00 |
| New York Open | 15:00 – 17:00 |
| New York Close | 20:00 – 22:00 |

---

## 📱 Optional: Telegram Alerts

1. Create a bot with [@BotFather](https://t.me/BotFather) on Telegram
2. Copy your bot token
3. Get your chat ID from [@userinfobot](https://t.me/userinfobot)
4. Edit `config.py`:

```python
TELEGRAM_ENABLED = True
TELEGRAM_TOKEN   = "1234567890:ABCdefGhIjKlMnOpQrSt..."
TELEGRAM_CHAT_ID = "123456789"
```

---

## 🗂️ Trade Journal

Every signal is automatically saved to `journal/trade_journal.csv`.

After each trade closes, manually fill in the `outcome`, `pnl_pips`, and `notes` columns. Over time this builds a rich performance dataset.

---

## 🔧 Using MetaTrader 5 (Windows only)

1. Install MetaTrader 5 and log into your broker account
2. Install the Python package: `pip install MetaTrader5`
3. In `config.py`:

```python
DATA_SOURCE  = "mt5"
MT5_LOGIN    = 12345678
MT5_PASSWORD = "your_password"
MT5_SERVER   = "BrokerName-Demo"
SYMBOL       = "EURUSD"   # MT5 uses this directly
```

---

## ⚠️ Disclaimer

This bot is for **educational purposes only**. It does not constitute financial advice. Forex trading involves significant risk of loss. Always:
- Paper-trade first
- Understand every trade before taking it
- Use proper risk management
- Consult a financial advisor

---

## 📜 License

MIT License — free to use, modify, and distribute.
