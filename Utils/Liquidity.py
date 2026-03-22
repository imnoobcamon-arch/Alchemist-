"""
utils/liquidity.py
──────────────────
ICT Liquidity concepts:
  • Equal Highs / Equal Lows  (BSL / SSL pools)
  • Liquidity Sweeps (stop hunts)
  • Inducement detection
  • Previous Day / Week highs & lows
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import pandas as pd
import numpy as np

import config
from data.fetcher import compute_atr

logger = logging.getLogger(__name__)

# 1 pip for EURUSD in price terms
PIP = 0.0001
SWEEP_TOLERANCE = config.SWEEP_TOLERANCE_PIPS * PIP


# ─── Data structures ──────────────────────────────────────────────────────────

@dataclass
class LiquidityPool:
    """A resting pool of stop-loss orders sitting above/below equal H/L."""
    kind: str           # "BSL" (buy-side) | "SSL" (sell-side)
    price: float        # level where stops are clustered
    touches: int        # how many times price approached but did not break
    formed_at: pd.Timestamp
    swept: bool = False
    swept_at: Optional[pd.Timestamp] = None
    description: str = ""


@dataclass
class LiquiditySweep:
    """A confirmed sweep of a prior liquidity pool."""
    kind: str               # "BSL_sweep" | "SSL_sweep"
    pool_price: float       # the liquidity level that was swept
    sweep_high: float       # highest wick of the sweep candle
    sweep_low: float        # lowest wick of the sweep candle
    candle_close: float     # close of the sweep candle
    timestamp: pd.Timestamp
    closed_back: bool       # True when close is BACK inside the range → stronger signal
    description: str = ""


@dataclass
class PreviousLevels:
    """Prior day / prior week high and low."""
    prev_day_high:  Optional[float] = None
    prev_day_low:   Optional[float] = None
    prev_week_high: Optional[float] = None
    prev_week_low:  Optional[float] = None


# ─── Equal Highs / Lows ───────────────────────────────────────────────────────

def detect_liquidity_pools(
    df: pd.DataFrame,
    tolerance_pips: float = 3.0
) -> List[LiquidityPool]:
    """
    Identify equal highs (Buy-Side Liquidity) and equal lows (Sell-Side Liquidity).

    Logic:
      Two or more swing highs within `tolerance` of each other → BSL pool.
      Two or more swing lows  within `tolerance` of each other → SSL pool.
    """
    from utils.market_structure import detect_swings
    tolerance = tolerance_pips * PIP

    swing_highs, swing_lows = detect_swings(df)
    pools: List[LiquidityPool] = []

    def _cluster(points, kind):
        """Group swing points into clusters within tolerance."""
        prices = sorted(set(p.price for p in points))
        clusters = []
        current = [prices[0]]

        for px in prices[1:]:
            if px - current[-1] <= tolerance:
                current.append(px)
            else:
                if len(current) >= 2:
                    clusters.append(current)
                current = [px]
        if len(current) >= 2:
            clusters.append(current)

        result = []
        for cluster in clusters:
            level = np.mean(cluster)
            touches = len(cluster)
            # Find the earliest timestamp from the swing points in this cluster
            ts_points = [p for p in points if any(abs(p.price - c) <= tolerance for c in cluster)]
            formed_at = min(p.timestamp for p in ts_points) if ts_points else df.index[-1]
            result.append(LiquidityPool(
                kind        = kind,
                price       = float(level),
                touches     = touches,
                formed_at   = formed_at,
                description = f"{kind} pool at {level:.5f} ({touches} touches)"
            ))
        return result

    if swing_highs:
        pools += _cluster(swing_highs, "BSL")
    if swing_lows:
        pools += _cluster(swing_lows, "SSL")

    return pools


# ─── Sweep Detection ──────────────────────────────────────────────────────────

def detect_sweeps(
    df: pd.DataFrame,
    pools: List[LiquidityPool]
) -> List[LiquiditySweep]:
    """
    For each liquidity pool, scan for a candle that:
      1. Wicks BEYOND the pool level (triggering stops)
      2. Closes BACK inside (rejection / manipulation)

    This is the ICT "stop hunt" or "liquidity sweep" pattern.
    """
    sweeps: List[LiquiditySweep] = []
    atr = compute_atr(df)

    for pool in pools:
        if pool.swept:
            continue  # already marked

        # Only look at candles formed AFTER the pool
        mask = df.index > pool.formed_at
        future = df[mask]

        for ts, row in future.iterrows():
            if pool.kind == "BSL":
                # Buy-side liquidity: stops ABOVE the pool → wick above it
                wick_beyond = row["High"] > pool.price + SWEEP_TOLERANCE
                # Closed back below → confirmation of manipulation
                closed_back = row["Close"] < pool.price
                if wick_beyond:
                    sweep = LiquiditySweep(
                        kind          = "BSL_sweep",
                        pool_price    = pool.price,
                        sweep_high    = float(row["High"]),
                        sweep_low     = float(row["Low"]),
                        candle_close  = float(row["Close"]),
                        timestamp     = ts,
                        closed_back   = closed_back,
                        description   = (
                            f"BSL sweep at {ts}: wick {row['High']:.5f} > pool {pool.price:.5f}"
                            + (" [CLOSE BACK ✓]" if closed_back else "")
                        )
                    )
                    sweeps.append(sweep)
                    pool.swept    = True
                    pool.swept_at = ts
                    break  # one sweep per pool

            elif pool.kind == "SSL":
                # Sell-side liquidity: stops BELOW the pool → wick below it
                wick_beyond = row["Low"] < pool.price - SWEEP_TOLERANCE
                closed_back = row["Close"] > pool.price
                if wick_beyond:
                    sweep = LiquiditySweep(
                        kind          = "SSL_sweep",
                        pool_price    = pool.price,
                        sweep_high    = float(row["High"]),
                        sweep_low     = float(row["Low"]),
                        candle_close  = float(row["Close"]),
                        timestamp     = ts,
                        closed_back   = closed_back,
                        description   = (
                            f"SSL sweep at {ts}: wick {row['Low']:.5f} < pool {pool.price:.5f}"
                            + (" [CLOSE BACK ✓]" if closed_back else "")
                        )
                    )
                    sweeps.append(sweep)
                    pool.swept    = True
                    pool.swept_at = ts
                    break

    return sweeps


# ─── Inducement ───────────────────────────────────────────────────────────────

def detect_inducement(
    df: pd.DataFrame,
    swing_highs,
    swing_lows,
    bias: str
) -> Optional[float]:
    """
    ICT Inducement: a minor structural point that entices retail traders into
    a losing position, allowing smart money to accumulate before the real move.

    For a BULLISH setup:
      A minor swing low forms ABOVE a major swing low → inducement below it.
      Retail traders place stops below that minor swing low.

    For a BEARISH setup:
      A minor swing high forms BELOW a major swing high → inducement above it.

    Returns the price level of the inducement, or None.
    """
    if bias == "bullish" and len(swing_lows) >= 2:
        # The most recent swing low is the inducement level for longs
        recent_low = swing_lows[-1].price
        prev_low   = swing_lows[-2].price
        if recent_low > prev_low:
            # Minor low is higher → it's an inducement above the major low
            logger.debug(f"Bullish inducement at {recent_low:.5f}")
            return recent_low

    elif bias == "bearish" and len(swing_highs) >= 2:
        recent_high = swing_highs[-1].price
        prev_high   = swing_highs[-2].price
        if recent_high < prev_high:
            logger.debug(f"Bearish inducement at {recent_high:.5f}")
            return recent_high

    return None


# ─── Previous Day / Week Levels ───────────────────────────────────────────────

def get_previous_levels(df_d1: pd.DataFrame) -> PreviousLevels:
    """
    Extract the prior day and prior week high/low from D1 data.

    These act as major liquidity targets (stops cluster above/below them).
    """
    if df_d1 is None or df_d1.empty or len(df_d1) < 2:
        return PreviousLevels()

    prev_day = df_d1.iloc[-2]   # second-to-last daily bar
    prev_day_high = float(prev_day["High"])
    prev_day_low  = float(prev_day["Low"])

    # ── Prior week: find the last Monday and go back one full week
    df_d1 = df_d1.copy()
    df_d1.index = pd.to_datetime(df_d1.index)
    df_d1["week"] = df_d1.index.isocalendar().week.values
    grouped = df_d1.groupby("week")

    week_keys = sorted(grouped.groups.keys())
    if len(week_keys) >= 2:
        prev_week_data = grouped.get_group(week_keys[-2])
        prev_week_high = float(prev_week_data["High"].max())
        prev_week_low  = float(prev_week_data["Low"].min())
    else:
        prev_week_high = None
        prev_week_low  = None

    return PreviousLevels(
        prev_day_high  = prev_day_high,
        prev_day_low   = prev_day_low,
        prev_week_high = prev_week_high,
        prev_week_low  = prev_week_low,
    )


# ─── Public convenience wrapper ───────────────────────────────────────────────

def analyse(df: pd.DataFrame, df_d1: pd.DataFrame = None) -> dict:
    """
    Run the complete liquidity analysis pipeline.

    Returns
    -------
    dict with keys:
      pools      : List[LiquidityPool]
      sweeps     : List[LiquiditySweep]
      prev_levels: PreviousLevels
      last_sweep : LiquiditySweep | None  (most recent sweep if any)
    """
    pools       = detect_liquidity_pools(df)
    sweeps      = detect_sweeps(df, pools)
    prev_levels = get_previous_levels(df_d1) if df_d1 is not None else PreviousLevels()
    last_sweep  = sweeps[-1] if sweeps else None

    logger.info(f"Liquidity: {len(pools)} pools, {len(sweeps)} sweeps detected")
    if last_sweep:
        logger.info(f"  → Last sweep: {last_sweep.description}")

    return {
        "pools":       pools,
        "sweeps":      sweeps,
        "prev_levels": prev_levels,
        "last_sweep":  last_sweep,
    }
