"""import os
config.py — Central configuration for the ICT Forex Bot
All settings live here. Edit this file to customize behaviour.
"""

# ─── Pair & Timeframes ────────────────────────────────────────────────────────
SYMBOL          = "EURUSD=X"   # Yahoo Finance ticker  (use "EURUSD" for MT5)
PAIR_DISPLAY    = "EURUSD"

# Timeframes used throughout the bot (yfinance notation)
TIMEFRAMES = {
    "D1":  "1d",
    "H4":  "1h",   # yfinance free tier → 1h is the finest; we aggregate to 4H
    "H1":  "1h",
    "M30": "30m",
    "M5":  "5m",
}

# How many candles to fetch per timeframe
CANDLE_LOOKBACK = {
    "D1":  60,
    "H4":  120,
    "H1":  168,   # 1 week of hourly
    "M30": 200,
    "M5":  500,
}

# ─── Session Windows (UTC+3) ──────────────────────────────────────────────────
# UTC+3  =  UTC + 3 hours
SESSIONS = {
    "Asian":    {"start": "02:00", "end": "09:00"},   # UTC+3
    "London":   {"start": "10:00", "end": "18:00"},   # UTC+3  (07:00–15:00 UTC)
    "NewYork":  {"start": "15:00", "end": "23:00"},   # UTC+3  (12:00–20:00 UTC)
}

# Kill-zone windows (highest-probability entry periods)
KILLZONES = {
    "London_Open":   {"start": "10:00", "end": "12:00"},
    "NewYork_Open":  {"start": "15:00", "end": "17:00"},
    "NewYork_Close": {"start": "20:00", "end": "22:00"},
}

# ─── ICT Logic Parameters ─────────────────────────────────────────────────────
# Minimum candles to look back when detecting swing highs/lows
SWING_LOOKBACK = 5

# Minimum FVG size as fraction of the H1 ATR (filters noise)
FVG_MIN_ATR_FRACTION = 0.3

# Order Block: minimum candle body ratio to qualify (body / total range)
OB_MIN_BODY_RATIO = 0.4

# Liquidity sweep tolerance — how many pips beyond prev high/low counts as a sweep
SWEEP_TOLERANCE_PIPS = 3

# Premium / Discount threshold around equilibrium (50 % of range)
PREMIUM_DISCOUNT_THRESHOLD = 0.10   # 10 % either side of mid-range

# ─── Risk Management ──────────────────────────────────────────────────────────
DEFAULT_RISK_PERCENT   = 1.0    # % of account risked per trade
DEFAULT_ACCOUNT_BALANCE= 10_000 # USD — used in position-size calculations
MIN_RR_RATIO           = 2.0    # Minimum Risk:Reward to take a trade
MAX_SETUPS_PER_SESSION = 2      # Hard cap on signals per session

# ─── Data Source ──────────────────────────────────────────────────────────────
DATA_SOURCE = "yfinance"        # "yfinance" | "mt5"
# MT5 settings (only used when DATA_SOURCE == "mt5")
MT5_LOGIN    = 0
MT5_PASSWORD = ""
MT5_SERVER   = ""

# ─── Optional Telegram Alerts ────────────────────────────────────────────────
TELEGRAM_ENABLED = True
TELEGRAM_TOKEN   = os.environ.get("8339428632:AAHR7qWF1vkFt3GT_51WdxKjPcUD5Ddz8uU", "")
TELEGRAM_CHAT_ID = "100-8406560308"

# ─── Logging ──────────────────────────────────────────────────────────────────
LOG_DIR   = "logs"
LOG_LEVEL = "INFO"     # DEBUG | INFO | WARNING | ERROR

# ─── Trade Journal ────────────────────────────────────────────────────────────
JOURNAL_FILE = "journal/trade_journal.csv"
