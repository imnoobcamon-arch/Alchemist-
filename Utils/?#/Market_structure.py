"""
utils/market_structure.py
─────────────────────────
ICT Market Structure detection:
  • Swing Highs / Swing Lows
  • Break of Structure (BOS)
  • Market Structure Shift (MSS)
  • Premium / Discount zones
  • Higher-Timeframe Bias (bullish / bearish / ranging)
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

import config

logger = logging.getLogger(__name__)


# ─── Data structures ──────────────────────────────────────────────────────────

@dataclass
class SwingPoint:
    index: int            # positional index in the DataFrame
    timestamp: pd.Timestamp
    price: float
    kind: str             # "high" | "low"


@dataclass
class StructureEvent:
    """Represents a BOS or MSS event."""
    kind: str             # "BOS" | "MSS"
    direction: str        # "bullish" | "bearish"
    broken_price: float   # the swing level that was broken
    break_candle_ts: pd.Timestamp
    description: str = ""


@dataclass
class MarketStructure:
    bias: str                              # "bullish" | "bearish" | "ranging"
    swing_highs: List[SwingPoint] = field(default_factory=list)
    swing_lows:  List[SwingPoint] = field(default_factory=list)
    bos_events:  List[StructureEvent] = field(default_factory=list)
    mss_events:  List[StructureEvent] = field(default_factory=list)
    premium_level:  Optional[float] = None
    discount_level: Optional[float] = None
    equilibrium:    Optional[float] = None
    range_high:     Optional[float] = None
    range_low:      Optional[float] = None


# ─── Swing Detection ──────────────────────────────────────────────────────────

def detect_swings(df: pd.DataFrame, lookback: int = None) -> Tuple[List[SwingPoint], List[SwingPoint]]:
    """
    Identify swing highs and swing lows using a rolling-window approach.

    A candle is a swing high if its High is the highest in the surrounding
    `lookback` candles on each side.  Symmetric for swing lows.

    Parameters
    ----------
    df       : OHLCV DataFrame
    lookback : number of candles on each side to confirm the swing

    Returns
    -------
    (swing_highs, swing_lows)  each a list of SwingPoint
    """
    if lookback is None:
        lookback = config.SWING_LOOKBACK

    highs: List[SwingPoint] = []
    lows:  List[SwingPoint] = []

    n = len(df)

    for i in range(lookback, n - lookback):
        window_high = df["High"].iloc[i - lookback: i + lookback + 1]
        window_low  = df["Low"].iloc[i - lookback: i + lookback + 1]

        # Swing high: this candle's High is the maximum in the window
        if df["High"].iloc[i] == window_high.max():
            highs.append(SwingPoint(
                index     = i,
                timestamp = df.index[i],
                price     = float(df["High"].iloc[i]),
                kind      = "high"
            ))

        # Swing low: this candle's Low is the minimum in the window
        if df["Low"].iloc[i] == window_low.min():
            lows.append(SwingPoint(
                index     = i,
                timestamp = df.index[i],
                price     = float(df["Low"].iloc[i]),
                kind      = "low"
            ))

    # Remove consecutive duplicates (keep the most extreme in a cluster)
    highs = _deduplicate_swings(highs, keep="max")
    lows  = _deduplicate_swings(lows,  keep="min")

    return highs, lows


def _deduplicate_swings(
    points: List[SwingPoint],
    keep: str = "max",
    proximity: int = 3
) -> List[SwingPoint]:
    """Remove swing points that are too close together, keeping the extreme."""
    if not points:
        return points

    result = [points[0]]
    for pt in points[1:]:
        if pt.index - result[-1].index <= proximity:
            # Within proximity: keep the more extreme one
            if keep == "max" and pt.price > result[-1].price:
                result[-1] = pt
            elif keep == "min" and pt.price < result[-1].price:
                result[-1] = pt
        else:
            result.append(pt)
    return result


# ─── BOS / MSS Detection ──────────────────────────────────────────────────────

def detect_bos_mss(
    df: pd.DataFrame,
    swing_highs: List[SwingPoint],
    swing_lows:  List[SwingPoint]
) -> Tuple[List[StructureEvent], List[StructureEvent]]:
    """
    Break of Structure (BOS):
        Price closes *beyond* a prior swing high/low in the direction of the
        existing trend — confirming trend continuation.

    Market Structure Shift (MSS):
        Price closes *beyond* a swing point that goes AGAINST the prior trend —
        signalling a potential reversal.  ICT uses this as a key entry trigger.

    Returns
    -------
    (bos_events, mss_events)
    """
    bos_events: List[StructureEvent] = []
    mss_events: List[StructureEvent] = []

    close = df["Close"]

    # ── BOS: bullish → price breaks above the most recent swing HIGH
    for sh in swing_highs:
        # Look for the first close after this swing high that exceeds it
        future = df.iloc[sh.index + 1:]
        for idx, row in future.iterrows():
            if row["Close"] > sh.price:
                event = StructureEvent(
                    kind          = "BOS",
                    direction     = "bullish",
                    broken_price  = sh.price,
                    break_candle_ts = idx,
                    description   = f"Bullish BOS: close {row['Close']:.5f} > swing high {sh.price:.5f}"
                )
                bos_events.append(event)
                break   # only first break per swing

    # ── BOS: bearish → price breaks below the most recent swing LOW
    for sl in swing_lows:
        future = df.iloc[sl.index + 1:]
        for idx, row in future.iterrows():
            if row["Close"] < sl.price:
                event = StructureEvent(
                    kind          = "BOS",
                    direction     = "bearish",
                    broken_price  = sl.price,
                    break_candle_ts = idx,
                    description   = f"Bearish BOS: close {row['Close']:.5f} < swing low {sl.price:.5f}"
                )
                bos_events.append(event)
                break

    # ── MSS: after a DOWN move, price breaks above a swing HIGH → bullish shift
    #        after an UP   move, price breaks below a swing LOW  → bearish shift
    # We use the last two swing points to infer prior trend.
    all_swings = sorted(swing_highs + swing_lows, key=lambda s: s.index)

    for i in range(1, len(all_swings)):
        prev = all_swings[i - 1]
        curr = all_swings[i]

        # Prior bearish leg (high → low): watch for price breaking ABOVE that high → bullish MSS
        if prev.kind == "high" and curr.kind == "low":
            future = df.iloc[curr.index + 1:]
            for idx, row in future.iterrows():
                if row["Close"] > prev.price:
                    event = StructureEvent(
                        kind          = "MSS",
                        direction     = "bullish",
                        broken_price  = prev.price,
                        break_candle_ts = idx,
                        description   = (
                            f"Bullish MSS: after bearish leg, close {row['Close']:.5f} "
                            f"broke above prior high {prev.price:.5f}"
                        )
                    )
                    mss_events.append(event)
                    break

        # Prior bullish leg (low → high): watch for price breaking BELOW that low → bearish MSS
        if prev.kind == "low" and curr.kind == "high":
            future = df.iloc[curr.index + 1:]
            for idx, row in future.iterrows():
                if row["Close"] < prev.price:
                    event = StructureEvent(
                        kind          = "MSS",
                        direction     = "bearish",
                        broken_price  = prev.price,
                        break_candle_ts = idx,
                        description   = (
                            f"Bearish MSS: after bullish leg, close {row['Close']:.5f} "
                            f"broke below prior low {prev.price:.5f}"
                        )
                    )
                    mss_events.append(event)
                    break

    return bos_events, mss_events


# ─── Bias & Premium / Discount ───────────────────────────────────────────────

def determine_bias(
    bos_events: List[StructureEvent],
    mss_events: List[StructureEvent],
    n_recent: int = 5
) -> str:
    """
    Determine the overall market bias from recent BOS/MSS events.

    Rules:
      • If the last MSS is bullish → bullish bias
      • If the last MSS is bearish → bearish bias
      • Otherwise count recent BOS direction
    """
    # Most recent MSS always wins
    if mss_events:
        last_mss = mss_events[-1]
        return last_mss.direction

    # Fall back to majority BOS direction
    recent_bos = bos_events[-n_recent:]
    if not recent_bos:
        return "ranging"

    bull = sum(1 for e in recent_bos if e.direction == "bullish")
    bear = len(recent_bos) - bull

    if bull > bear:
        return "bullish"
    elif bear > bull:
        return "bearish"
    return "ranging"


def compute_premium_discount(df: pd.DataFrame, lookback: int = 50) -> dict:
    """
    Calculate the current range's equilibrium (50 % level), premium zone
    (above equilibrium) and discount zone (below equilibrium).

    ICT Rule:
      • Look for SELLS in premium  (price is expensive)
      • Look for BUYS  in discount (price is cheap)

    Parameters
    ----------
    df       : OHLCV DataFrame (typically H4 or D1)
    lookback : candles to define the range

    Returns
    -------
    dict with keys: range_high, range_low, equilibrium,
                    premium_level, discount_level, current_zone
    """
    recent = df.tail(lookback)
    range_high = float(recent["High"].max())
    range_low  = float(recent["Low"].min())
    equil      = (range_high + range_low) / 2

    threshold  = (range_high - range_low) * config.PREMIUM_DISCOUNT_THRESHOLD
    premium    = equil + threshold
    discount   = equil - threshold

    current_price = float(df["Close"].iloc[-1])
    if current_price > premium:
        zone = "premium"
    elif current_price < discount:
        zone = "discount"
    else:
        zone = "equilibrium"

    return {
        "range_high":     range_high,
        "range_low":      range_low,
        "equilibrium":    equil,
        "premium_level":  premium,
        "discount_level": discount,
        "current_zone":   zone,
        "current_price":  current_price,
    }


# ─── Full Analysis ────────────────────────────────────────────────────────────

def analyse(df: pd.DataFrame, lookback_pd: int = 50) -> MarketStructure:
    """
    Run the full market-structure pipeline on a single timeframe DataFrame.

    Parameters
    ----------
    df         : OHLCV DataFrame
    lookback_pd: candles for premium/discount range

    Returns
    -------
    MarketStructure object
    """
    swing_highs, swing_lows = detect_swings(df)
    bos_events, mss_events  = detect_bos_mss(df, swing_highs, swing_lows)
    bias                    = determine_bias(bos_events, mss_events)
    pd_data                 = compute_premium_discount(df, lookback_pd)

    ms = MarketStructure(
        bias            = bias,
        swing_highs     = swing_highs,
        swing_lows      = swing_lows,
        bos_events      = bos_events,
        mss_events      = mss_events,
        premium_level   = pd_data["premium_level"],
        discount_level  = pd_data["discount_level"],
        equilibrium     = pd_data["equilibrium"],
        range_high      = pd_data["range_high"],
        range_low       = pd_data["range_low"],
    )
    return ms
