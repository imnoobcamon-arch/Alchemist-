"""
utils/order_blocks.py
─────────────────────
ICT Order Block & Breaker Block detection.

Order Block (OB):
  The last opposing candle before a significant impulse move.
  • Bullish OB: the last bearish (down) candle before a strong upward impulse
  • Bearish OB: the last bullish (up) candle before a strong downward impulse
  Price often returns to the OB to fill institutional orders before continuing.

Breaker Block:
  An order block that price has already traded through (its orders were filled),
  which then flips into resistance / support.
  • A bullish OB that gets broken to the downside → becomes bearish breaker
  • A bearish OB that gets broken to the upside  → becomes bullish breaker
"""

import logging
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd
import numpy as np

import config
from data.fetcher import compute_atr

logger = logging.getLogger(__name__)

PIP = 0.0001


# ─── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class OrderBlock:
    kind: str             # "bullish" | "bearish"
    top: float            # upper boundary of the OB candle
    bottom: float         # lower boundary of the OB candle
    open_: float          # candle open
    close_: float         # candle close
    formed_at: pd.Timestamp
    impulse_strength: float = 0.0   # move size after the OB in ATR multiples
    mitigated: bool = False         # True once price has traded back into the OB
    is_breaker: bool = False        # True if OB flipped to breaker
    description: str = ""


@dataclass
class BreakerBlock:
    kind: str             # "bullish" | "bearish"  (new direction after flip)
    top: float
    bottom: float
    formed_at: pd.Timestamp
    original_ob: OrderBlock
    description: str = ""


# ─── Detection ────────────────────────────────────────────────────────────────

def detect_order_blocks(
    df: pd.DataFrame,
    impulse_atr_multiple: float = 1.5,
    min_body_ratio: float = None
) -> List[OrderBlock]:
    """
    Identify order blocks in the DataFrame.

    Algorithm:
      1. For each candle, check if the NEXT N candles form a strong impulse
         (move > impulse_atr_multiple × ATR).
      2. The candle immediately BEFORE the impulse is the OB if:
         - Its body ratio exceeds min_body_ratio (it's a real candle, not a doji)
         - Its direction is OPPOSITE to the impulse

    Parameters
    ----------
    df                   : OHLCV DataFrame
    impulse_atr_multiple : how many ATRs the impulse must cover
    min_body_ratio       : minimum body/range ratio for the OB candle

    Returns
    -------
    List[OrderBlock] sorted newest-first
    """
    if min_body_ratio is None:
        min_body_ratio = config.OB_MIN_BODY_RATIO

    atr      = compute_atr(df)
    min_move = atr * impulse_atr_multiple

    obs: List[OrderBlock] = []
    n = len(df)
    look_forward = 5   # candles to look ahead for the impulse

    for i in range(1, n - look_forward):
        candle = df.iloc[i]
        body   = abs(candle["Close"] - candle["Open"])
        rng    = candle["High"] - candle["Low"]
        if rng == 0:
            continue
        body_ratio = body / rng

        if body_ratio < min_body_ratio:
            continue    # doji / inside bar — skip

        is_bearish_candle = candle["Close"] < candle["Open"]
        is_bullish_candle = candle["Close"] > candle["Open"]

        # Measure forward move over the next `look_forward` candles
        future = df.iloc[i + 1: i + 1 + look_forward]
        fwd_high = future["High"].max()
        fwd_low  = future["Low"].min()

        upward_move   = fwd_high - candle["High"]
        downward_move = candle["Low"] - fwd_low

        # ── Bullish OB: bearish candle + strong upward impulse follows
        if is_bearish_candle and upward_move >= min_move:
            ob = OrderBlock(
                kind             = "bullish",
                top              = float(candle["High"]),
                bottom           = float(candle["Low"]),
                open_            = float(candle["Open"]),
                close_           = float(candle["Close"]),
                formed_at        = df.index[i],
                impulse_strength = upward_move / atr,
                description      = (
                    f"Bullish OB [{candle['Low']:.5f}–{candle['High']:.5f}] "
                    f"at {df.index[i]} | impulse {upward_move / atr:.1f}× ATR"
                )
            )
            obs.append(ob)

        # ── Bearish OB: bullish candle + strong downward impulse follows
        elif is_bullish_candle and downward_move >= min_move:
            ob = OrderBlock(
                kind             = "bearish",
                top              = float(candle["High"]),
                bottom           = float(candle["Low"]),
                open_            = float(candle["Open"]),
                close_           = float(candle["Close"]),
                formed_at        = df.index[i],
                impulse_strength = downward_move / atr,
                description      = (
                    f"Bearish OB [{candle['Low']:.5f}–{candle['High']:.5f}] "
                    f"at {df.index[i]} | impulse {downward_move / atr:.1f}× ATR"
                )
            )
            obs.append(ob)

    # Mark mitigated OBs and detect breakers
    obs = _mark_mitigation(df, obs)

    return sorted(obs, key=lambda o: o.formed_at, reverse=True)


def _mark_mitigation(df: pd.DataFrame, obs: List[OrderBlock]) -> List[OrderBlock]:
    """
    An OB is 'mitigated' once price trades back into its range.
    A mitigated OB that then gets broken becomes a Breaker Block.
    """
    for ob in obs:
        future = df[df.index > ob.formed_at]
        for ts, row in future.iterrows():
            if ob.kind == "bullish":
                # Mitigation: price pulls back into the OB zone from above
                if row["Low"] <= ob.top and row["High"] >= ob.bottom:
                    ob.mitigated = True
                # Breaker: price closes BELOW the OB low after mitigation
                if ob.mitigated and row["Close"] < ob.bottom:
                    ob.is_breaker = True
                    break
            else:  # bearish
                # Mitigation: price pulls back into the OB zone from below
                if row["High"] >= ob.bottom and row["Low"] <= ob.top:
                    ob.mitigated = True
                # Breaker: price closes ABOVE the OB high after mitigation
                if ob.mitigated and row["Close"] > ob.top:
                    ob.is_breaker = True
                    break
    return obs


def detect_breaker_blocks(obs: List[OrderBlock]) -> List[BreakerBlock]:
    """
    Extract Breaker Blocks from a list of (potentially flipped) OBs.

    A breaker flips direction:
      • Bullish OB that broke → bearish breaker (resistance)
      • Bearish OB that broke → bullish breaker (support)
    """
    breakers: List[BreakerBlock] = []
    for ob in obs:
        if not ob.is_breaker:
            continue

        new_kind = "bearish" if ob.kind == "bullish" else "bullish"
        bb = BreakerBlock(
            kind        = new_kind,
            top         = ob.top,
            bottom      = ob.bottom,
            formed_at   = ob.formed_at,
            original_ob = ob,
            description = (
                f"{new_kind.title()} Breaker [{ob.bottom:.5f}–{ob.top:.5f}] "
                f"(flipped from {ob.kind} OB at {ob.formed_at})"
            )
        )
        breakers.append(bb)
    return breakers


# ─── Helpers ──────────────────────────────────────────────────────────────────

def nearest_ob(
    price: float,
    obs: List[OrderBlock],
    direction: str = "bullish"
) -> Optional[OrderBlock]:
    """
    Return the closest un-mitigated order block in the given direction.

    For LONG trade  → bullish OB below current price
    For SHORT trade → bearish OB above current price
    """
    aligned = [o for o in obs if o.kind == direction and not o.is_breaker]

    if direction == "bullish":
        candidates = [o for o in aligned if o.top <= price]
        if not candidates:
            return None
        return max(candidates, key=lambda o: o.top)

    else:
        candidates = [o for o in aligned if o.bottom >= price]
        if not candidates:
            return None
        return min(candidates, key=lambda o: o.bottom)


def price_inside_ob(price: float, ob: OrderBlock) -> bool:
    """Return True if `price` is inside the OB range."""
    return ob.bottom <= price <= ob.top
