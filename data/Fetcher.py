"""
data/fetcher.py — Price data retrieval layer
Supports yfinance (default / free) and MetaTrader 5 (optional).
"""

import logging
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

import config

logger = logging.getLogger(__name__)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _resample_to_4h(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Resample 1-hour OHLCV data into 4-hour bars."""
    df = df_1h.copy()
    df.index = pd.to_datetime(df.index)
    df_4h = df.resample("4h").agg({
        "Open":   "first",
        "High":   "max",
        "Low":    "min",
        "Close":  "last",
        "Volume": "sum",
    }).dropna()
    return df_4h


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Standardise column names and drop rows with nulls."""
    df.columns = [c.strip().title() for c in df.columns]
    # yfinance sometimes returns multi-level columns
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.dropna(inplace=True)
    df.index = pd.to_datetime(df.index)
    return df


# ─── yfinance backend ─────────────────────────────────────────────────────────

def _fetch_yfinance(timeframe_key: str, n_candles: int) -> pd.DataFrame:
    """
    Download OHLCV from Yahoo Finance.

    timeframe_key: one of the keys in config.TIMEFRAMES  ("D1", "H4", …)
    """
    try:
        import yfinance as yf
    except ImportError:
        raise ImportError("yfinance is not installed. Run: pip install yfinance")

    tf_str = config.TIMEFRAMES[timeframe_key]

    # Determine period string from candle count + interval
    interval_to_days = {
        "1d":  lambda n: n + 5,
        "1h":  lambda n: max(int(n / 24) + 5, 8),
        "30m": lambda n: max(int(n / 48) + 3, 7),
        "5m":  lambda n: max(int(n / 288) + 2, 5),
    }
    days = interval_to_days.get(tf_str, lambda n: 30)(n_candles)
    start = datetime.utcnow() - timedelta(days=days)

    ticker = yf.Ticker(config.SYMBOL)
    df = ticker.history(start=start.strftime("%Y-%m-%d"), interval=tf_str)

    if df.empty:
        logger.warning(f"[yfinance] No data returned for {config.SYMBOL} / {tf_str}")
        return pd.DataFrame()

    df = _clean(df)

    # 4H is not a native yfinance interval → resample from 1H
    if timeframe_key == "H4":
        df = _resample_to_4h(df)

    return df.tail(n_candles)


# ─── MetaTrader 5 backend ─────────────────────────────────────────────────────

def _fetch_mt5(timeframe_key: str, n_candles: int) -> pd.DataFrame:
    """
    Fetch OHLCV from a running MetaTrader 5 terminal.
    Requires:  pip install MetaTrader5
    Only works on Windows with MT5 installed.
    """
    try:
        import MetaTrader5 as mt5
    except ImportError:
        raise ImportError("MetaTrader5 package not found. pip install MetaTrader5")

    tf_map = {
        "D1":  mt5.TIMEFRAME_D1,
        "H4":  mt5.TIMEFRAME_H4,
        "H1":  mt5.TIMEFRAME_H1,
        "M30": mt5.TIMEFRAME_M30,
        "M5":  mt5.TIMEFRAME_M5,
    }
    if not mt5.initialize(
        login=config.MT5_LOGIN,
        password=config.MT5_PASSWORD,
        server=config.MT5_SERVER
    ):
        raise ConnectionError(f"MT5 init failed: {mt5.last_error()}")

    rates = mt5.copy_rates_from_pos(
        config.SYMBOL, tf_map[timeframe_key], 0, n_candles
    )
    mt5.shutdown()

    if rates is None or len(rates) == 0:
        logger.warning(f"[MT5] No data for {config.SYMBOL} / {timeframe_key}")
        return pd.DataFrame()

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    df.rename(columns={
        "open": "Open", "high": "High",
        "low":  "Low",  "close": "Close",
        "tick_volume": "Volume"
    }, inplace=True)
    return df[["Open", "High", "Low", "Close", "Volume"]]


# ─── Public API ───────────────────────────────────────────────────────────────

def fetch_ohlcv(timeframe_key: str, n_candles: int = None) -> pd.DataFrame:
    """
    Unified entry point — routes to the configured backend.

    Parameters
    ----------
    timeframe_key : str
        One of "D1", "H4", "H1", "M30", "M5"
    n_candles : int, optional
        Defaults to config.CANDLE_LOOKBACK[timeframe_key]

    Returns
    -------
    pd.DataFrame with columns [Open, High, Low, Close, Volume]
    """
    if n_candles is None:
        n_candles = config.CANDLE_LOOKBACK.get(timeframe_key, 100)

    logger.info(f"Fetching {timeframe_key} data ({n_candles} bars) via {config.DATA_SOURCE}")

    if config.DATA_SOURCE == "yfinance":
        return _fetch_yfinance(timeframe_key, n_candles)
    elif config.DATA_SOURCE == "mt5":
        return _fetch_mt5(timeframe_key, n_candles)
    else:
        raise ValueError(f"Unknown DATA_SOURCE: {config.DATA_SOURCE}")


def fetch_all_timeframes() -> dict:
    """
    Convenience wrapper — returns a dict of DataFrames for all timeframes.

    Returns
    -------
    {
      "D1":  DataFrame,
      "H4":  DataFrame,
      "H1":  DataFrame,
      "M30": DataFrame,
      "M5":  DataFrame,
    }
    """
    result = {}
    for tf_key in config.TIMEFRAMES:
        try:
            df = fetch_ohlcv(tf_key)
            if not df.empty:
                result[tf_key] = df
                logger.info(f"  ✓ {tf_key}: {len(df)} candles, last close = {df['Close'].iloc[-1]:.5f}")
            else:
                logger.warning(f"  ✗ {tf_key}: empty dataframe returned")
        except Exception as e:
            logger.error(f"  ✗ {tf_key}: {e}")
    return result


def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    """
    Average True Range — used to size FVG / OB filters.

    Parameters
    ----------
    df     : OHLCV DataFrame
    period : lookback period (default 14)

    Returns
    -------
    float : most recent ATR value
    """
    high  = df["High"]
    low   = df["Low"]
    close = df["Close"]

    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs()
    ], axis=1).max(axis=1)

    atr = tr.ewm(span=period, adjust=False).mean()
    return float(atr.iloc[-1])
