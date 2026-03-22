"""
utils/fvg.py
────────────
Fair Value Gap (FVG) / Imbalance detection.

ICT Definition:
  A Fair Value Gap occurs when three consecutive candles leave a price gap
  (imbalance) where the market did not trade on both sides:

  Bullish FVG (demand imbalance):
    Candle[i-1] Low  >  Candle[i+1] High
    → gap between the low of the prior candle and the high of the next candle

  Bearish FVG (supply imbalance):
    Candle[i-1] High <  Candle[i+1] Low
    → gap between the high of the prior candle and the low of the next candle

  Price tends to retrace into FVGs (fill the imbalance) before continuing.

Also detects:
  • Consequent Encroachment (CE): midpoint of the FVG (50% level)
  • Partially filled vs. unfilled FVGs
"""

import logging
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd
import numpy as np

import config
from data.fetcher import compute_atr

logger = logging.getLogger(__name__)


# ─── Data Structure ───────────────────────────────────────────────────────────

@dataclass
class FairValueGap:
    kind: str             # "bullish" | "bearish"
    top: float            # upper boundary of the gap
    bottom: float         # lower boundary of the gap
    midpoint: float       # consequent encroachment (CE)
    size: float           # gap size in price
    size_pips: float      # gap size in pips
    formed_at: pd.Timestamp   # timestamp of the middle candle
    filled: bool = False      # True once price trades fully through the gap
    partially_filled: bool = False
    description: str = ""


# ─── Detection ────────────────────────────────────────────────────────────────

def detect_fvg(
    df: pd.DataFrame,
    min_atr_fraction: float = None,
    only_unfilled: bool = True
) -> List[FairValueGap]:
    """
    Scan the DataFrame for Fair Value Gaps.

    Parameters
    ----------
    df               : OHLCV DataFrame
    min_atr_fraction : minimum FVG size as a fraction of ATR (filters micro-gaps)
    only_unfilled    : if True, return only FVGs that have NOT been fully filled

    Returns
    -------
    List[FairValueGap] sorted newest-first
    """
    if min_atr_fraction is None:
        min_atr_fraction = config.FVG_MIN_ATR_FRACTION

    atr      = compute_atr(df)
    min_size = atr * min_atr_fraction

    fvgs: List[FairValueGap] = []
    n = len(df)

    for i in range(1, n - 1):
        prev = df.iloc[i - 1]
        curr = df.iloc[i]
        nxt  = df.iloc[i + 1]

        # ── Bullish FVG ──────────────────────────────────────────────────
        # prev.Low > nxt.High  →  a gap above nxt and below prev
        if prev["Low"] > nxt["High"]:
            gap_bottom = float(nxt["High"])
            gap_top    = float(prev["Low"])
            gap_size   = gap_top - gap_bottom

            if gap_size >= min_size:
                fvg = FairValueGap(
                    kind       = "bullish",
                    top        = gap_top,
                    bottom     = gap_bottom,
                    midpoint   = (gap_top + gap_bottom) / 2,
                    size       = gap_size,
                    size_pips  = gap_size / 0.0001,
                    formed_at  = df.index[i],
                    description= (
                        f"Bullish FVG [{gap_bottom:.5f} – {gap_top:.5f}] "
                        f"({gap_size / 0.0001:.1f} pips) at {df.index[i]}"
                    )
                )
                fvgs.append(fvg)

        # ── Bearish FVG ──────────────────────────────────────────────────
        # prev.High < nxt.Low  →  a gap below nxt and above prev
        elif prev["High"] < nxt["Low"]:
            gap_bottom = float(prev["High"])
            gap_top    = float(nxt["Low"])
            gap_size   = gap_top - gap_bottom

            if gap_size >= min_size:
                fvg = FairValueGap(
                    kind       = "bearish",
                    top        = gap_top,
                    bottom     = gap_bottom,
                    midpoint   = (gap_top + gap_bottom) / 2,
                    size       = gap_size,
                    size_pips  = gap_size / 0.0001,
                    formed_at  = df.index[i],
                    description= (
                        f"Bearish FVG [{gap_bottom:.5f} – {gap_top:.5f}] "
                        f"({gap_size / 0.0001:.1f} pips) at {df.index[i]}"
                    )
                )
                fvgs.append(fvg)

    # ── Mark filled FVGs ────────────────────────────────────────────────────
    fvgs = _mark_filled(df, fvgs)

    if only_unfilled:
        fvgs = [f for f in fvgs if not f.filled]

    # Return newest first
    return sorted(fvgs, key=lambda f: f.formed_at, reverse=True)


def _mark_filled(df: pd.DataFrame, fvgs: List[FairValueGap]) -> List[FairValueGap]:
    """
    Check whether subsequent price action has filled each FVG.

    Full fill   : price traded through the entire gap (High > top OR Low < bottom)
    Partial fill: price entered the gap but did not fully close it
    """
    for fvg in fvgs:
        future = df[df.index > fvg.formed_at]
        for _, row in future.iterrows():
            if fvg.kind == "bullish":
                # A bullish FVG is filled when price drops into it from above
                if row["Low"] <= fvg.bottom:
                    fvg.filled = True
                    break
                elif row["Low"] <= fvg.top:
                    fvg.partially_filled = True
            else:  # bearish
                if row["High"] >= fvg.top:
                    fvg.filled = True
                    break
                elif row["High"] >= fvg.bottom:
                    fvg.partially_filled = True
    return fvgs


# ─── Imbalance helpers ───────────────────────────────────────────────────────

def price_inside_fvg(price: float, fvg: FairValueGap) -> bool:
    """Return True if `price` is inside the FVG range."""
    return fvg.bottom <= price <= fvg.top


def nearest_fvg(
    price: float,
    fvgs: List[FairValueGap],
    direction: str = "bullish"
) -> Optional[FairValueGap]:
    """
    Find the FVG closest to `price` that aligns with `direction`.

    For a LONG trade  → we want a BULLISH FVG below current price to enter into.
    For a SHORT trade → we want a BEARISH FVG above current price to enter into.
    """
    aligned = [f for f in fvgs if f.kind == direction and not f.filled]

    if direction == "bullish":
        # FVG should be below or at current price
        candidates = [f for f in aligned if f.top <= price]
        if not candidates:
            return None
        return max(candidates, key=lambda f: f.top)   # closest below

    else:  # bearish
        # FVG should be above or at current price
        candidates = [f for f in aligned if f.bottom >= price]
        if not candidates:
            return None
        return min(candidates, key=lambda f: f.bottom)  # closest above


# ─── Summary ─────────────────────────────────────────────────────────────────

def summarise(fvgs: List[FairValueGap]) -> str:
    """Human-readable summary of detected FVGs."""
    if not fvgs:
        return "No unfilled FVGs detected."
    lines = [f"  {f.description}" for f in fvgs[:5]]  # top 5
    return "\n".join(lines)
