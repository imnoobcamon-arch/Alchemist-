"""
strategies/ict_strategy.py
──────────────────────────
Core ICT trade-setup engine.

Entry Model (strict checklist):
  ✅ 1. Higher-timeframe (D1) bias aligns  (bullish / bearish)
  ✅ 2. Liquidity swept on H4 / H1
  ✅ 3. MSS or BOS confirmed on H1 / M30
  ✅ 4. Price returns to FVG or Order Block (POI — Point of Interest)
  ✅ 5. Kill-zone active (London Open / NY Open)
  ✅ 6. 5M confirmation candle (engulfing or MSS)
  ✅ 7. Risk:Reward ≥ minimum threshold

The strategy returns a TradeSetup object or None if no valid setup exists.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict

import pandas as pd
import numpy as np

import config
from data.fetcher import compute_atr
from utils import market_structure, liquidity, fvg, order_blocks, sessions

logger = logging.getLogger(__name__)

PIP = 0.0001


# ─── Trade Setup dataclass ────────────────────────────────────────────────────

@dataclass
class TradeSetup:
    pair: str
    direction: str          # "BUY" | "SELL"
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    setup_time: str
    session: str
    killzone: str
    confidence: int         # 0–100

    # ICT reasoning
    htf_bias: str
    liquidity_swept: str
    structure_event: str
    point_of_interest: str
    reasoning: str

    # Risk management
    risk_pips: float
    reward_pips: float
    position_size_lots: float = 0.0

    # Supporting levels
    asian_range: dict = field(default_factory=dict)
    prev_day_high: Optional[float] = None
    prev_day_low: Optional[float] = None

    # Status
    valid: bool = True
    invalidation_note: str = ""


# ─── Entry Confirmation (5M) ──────────────────────────────────────────────────

def _confirm_5m_entry(
    df_5m: pd.DataFrame,
    poi_top: float,
    poi_bottom: float,
    direction: str
) -> Optional[dict]:
    """
    Look for a 5-minute confirmation candle inside or near the POI.

    Confirmation patterns:
      • Bullish: strong bullish engulfing candle after touching the POI zone
      • Bearish: strong bearish engulfing candle after touching the POI zone
      • MSS on 5M (optional, stronger signal)

    Returns dict with entry details or None if no confirmation found.
    """
    recent_5m = df_5m.tail(20)   # only look at the last 20 × 5M candles

    for i in range(1, len(recent_5m)):
        prev_c = recent_5m.iloc[i - 1]
        curr_c = recent_5m.iloc[i]

        if direction == "BUY":
            # Price must have touched the POI (bullish OB or FVG bottom)
            touched_poi = curr_c["Low"] <= poi_top and curr_c["Low"] >= poi_bottom * 0.999

            # Bullish engulfing: curr close > prev open, curr open < prev close
            is_engulfing = (
                curr_c["Close"] > prev_c["Open"] and
                curr_c["Open"] < prev_c["Close"] and
                curr_c["Close"] > curr_c["Open"]
            )

            # Strong bullish candle: body > 60% of range
            body  = curr_c["Close"] - curr_c["Open"]
            rng   = curr_c["High"] - curr_c["Low"]
            is_strong = body > 0 and rng > 0 and (body / rng) > 0.6

            if touched_poi and (is_engulfing or is_strong):
                return {
                    "entry"    : float(curr_c["High"]),   # enter on break of candle high
                    "candle_ts": recent_5m.index[i],
                    "pattern"  : "Bullish engulfing" if is_engulfing else "Strong bullish candle",
                }

        elif direction == "SELL":
            touched_poi = curr_c["High"] >= poi_bottom and curr_c["High"] <= poi_top * 1.001

            is_engulfing = (
                curr_c["Close"] < prev_c["Open"] and
                curr_c["Open"] > prev_c["Close"] and
                curr_c["Close"] < curr_c["Open"]
            )

            body  = curr_c["Open"] - curr_c["Close"]
            rng   = curr_c["High"] - curr_c["Low"]
            is_strong = body > 0 and rng > 0 and (body / rng) > 0.6

            if touched_poi and (is_engulfing or is_strong):
                return {
                    "entry"    : float(curr_c["Low"]),    # enter on break of candle low
                    "candle_ts": recent_5m.index[i],
                    "pattern"  : "Bearish engulfing" if is_engulfing else "Strong bearish candle",
                }

    return None


# ─── Stop Loss & Take Profit ──────────────────────────────────────────────────

def _calculate_sl(
    direction: str,
    poi_top: float,
    poi_bottom: float,
    swing_points,
    atr: float,
    buffer_pips: int = 5
) -> float:
    """
    Place the stop loss BEYOND the point of interest with a buffer.

    BUY  → SL below the POI bottom (or below the nearest swing low)
    SELL → SL above the POI top   (or above the nearest swing high)
    """
    buffer = buffer_pips * PIP

    if direction == "BUY":
        sl = poi_bottom - buffer
        # Optionally push SL below the nearest swing low that's below the POI
        nearby_lows = [p for p in swing_points if p.price < poi_bottom]
        if nearby_lows:
            deepest = min(nearby_lows, key=lambda p: p.price)
            sl = min(sl, deepest.price - buffer)
    else:
        sl = poi_top + buffer
        nearby_highs = [p for p in swing_points if p.price > poi_top]
        if nearby_highs:
            highest = max(nearby_highs, key=lambda p: p.price)
            sl = max(sl, highest.price + buffer)

    return round(sl, 5)


def _calculate_tp(
    direction: str,
    entry: float,
    swing_highs,
    swing_lows,
    liq_pools,
    prev_levels,
    risk_pips: float,
    min_rr: float = None
) -> float:
    """
    Set the take profit at the nearest significant liquidity above/below entry.

    Priority order:
      1. Previous day high/low (major liquidity)
      2. Nearest equal high/low pool (BSL/SSL)
      3. Swing extreme
      4. Minimum RR target (fallback)
    """
    if min_rr is None:
        min_rr = config.MIN_RR_RATIO

    min_tp_distance = risk_pips * min_rr * PIP
    candidates = []

    if direction == "BUY":
        # Targets above entry
        if prev_levels.prev_day_high and prev_levels.prev_day_high > entry + min_tp_distance:
            candidates.append(prev_levels.prev_day_high)

        for pool in liq_pools:
            if pool.kind == "BSL" and pool.price > entry + min_tp_distance:
                candidates.append(pool.price)

        for sh in swing_highs:
            if sh.price > entry + min_tp_distance:
                candidates.append(sh.price)

        tp = min(candidates) if candidates else entry + min_tp_distance

    else:
        # Targets below entry
        if prev_levels.prev_day_low and prev_levels.prev_day_low < entry - min_tp_distance:
            candidates.append(prev_levels.prev_day_low)

        for pool in liq_pools:
            if pool.kind == "SSL" and pool.price < entry - min_tp_distance:
                candidates.append(pool.price)

        for sl in swing_lows:
            if sl.price < entry - min_tp_distance:
                candidates.append(sl.price)

        tp = max(candidates) if candidates else entry - min_tp_distance

    return round(tp, 5)


def _position_size(entry: float, sl: float, risk_pct: float, balance: float) -> float:
    """
    Calculate position size in standard lots.

    Formula:
      risk_amount = balance × risk_pct / 100
      risk_pips   = |entry - sl| / PIP
      pip_value   ≈ $10 per standard lot for EURUSD
      lots        = risk_amount / (risk_pips × pip_value_per_lot)
    """
    pip_value_per_lot = 10.0     # USD per pip per standard lot for EURUSD
    risk_amount       = balance * risk_pct / 100
    risk_pips         = abs(entry - sl) / PIP
    if risk_pips == 0:
        return 0.0
    lots = risk_amount / (risk_pips * pip_value_per_lot)
    return round(lots, 2)


# ─── Confidence Scoring ───────────────────────────────────────────────────────

def _score_setup(
    htf_bias_match: bool,
    sweep_confirmed: bool,
    mss_confirmed: bool,
    in_killzone: bool,
    poi_type: str,            # "OB" | "FVG" | "both"
    rr_ratio: float,
    close_back: bool = False
) -> int:
    """
    Score the trade setup from 0 to 100.

    Each factor adds points. A score ≥ 70 is considered high-quality.
    """
    score = 0
    score += 25 if htf_bias_match   else 0
    score += 20 if sweep_confirmed  else 0
    score += 20 if mss_confirmed    else 0
    score += 15 if in_killzone      else 0
    score += 10 if poi_type == "both"  else (7 if poi_type in ("OB", "FVG") else 0)
    score += 5  if rr_ratio >= 3.0  else (3 if rr_ratio >= 2.0 else 0)
    score += 5  if close_back       else 0
    return min(score, 100)


# ─── Main Strategy Engine ─────────────────────────────────────────────────────

def analyse(data: dict) -> Optional[TradeSetup]:
    """
    Run the full ICT strategy checklist and return a TradeSetup (or None).

    Parameters
    ----------
    data : dict returned by data.fetcher.fetch_all_timeframes()

    Returns
    -------
    TradeSetup | None
    """
    # ── Extract timeframe data ─────────────────────────────────────────────
    df_d1  = data.get("D1")
    df_h4  = data.get("H4")
    df_h1  = data.get("H1")
    df_m30 = data.get("M30")
    df_m5  = data.get("M5")

    if df_d1 is None or df_h1 is None or df_m5 is None:
        logger.warning("Missing required timeframe data — skipping analysis")
        return None

    # ── Session & kill-zone check ──────────────────────────────────────────
    session_status = sessions.get_session_status()
    sessions.log_session_info(session_status)

    if session_status.is_weekend:
        logger.info("Weekend — market closed. No analysis.")
        return None

    # We still run analysis outside kill zones but log a warning
    if not session_status.in_killzone:
        logger.info("⚠️  Not in a kill zone — setup quality may be lower")

    # ── Step 1: D1 bias ────────────────────────────────────────────────────
    ms_d1  = market_structure.analyse(df_d1)
    htf_bias = ms_d1.bias
    logger.info(f"📊 D1 Bias: {htf_bias.upper()}")

    if htf_bias == "ranging":
        logger.info("D1 is ranging — no high-probability directional bias")
        return None

    direction = "BUY" if htf_bias == "bullish" else "SELL"

    # ── Step 2: H4 structure & premium/discount ────────────────────────────
    df_struct = df_h4 if df_h4 is not None else df_h1
    ms_h4     = market_structure.analyse(df_struct)
    pd_data   = market_structure.compute_premium_discount(df_struct)

    # For BUY: price should be in discount zone. For SELL: in premium zone.
    if direction == "BUY" and pd_data["current_zone"] == "premium":
        logger.info("🚫 BUY signal but price is in PREMIUM zone — skip")
        return None
    if direction == "SELL" and pd_data["current_zone"] == "discount":
        logger.info("🚫 SELL signal but price is in DISCOUNT zone — skip")
        return None

    logger.info(f"💹 Price zone: {pd_data['current_zone']} | EQ: {pd_data['equilibrium']:.5f}")

    # ── Step 3: H1 liquidity sweep check ──────────────────────────────────
    liq_data   = liquidity.analyse(df_h1, df_d1)
    last_sweep = liq_data["last_sweep"]
    prev_levels= liq_data["prev_levels"]
    liq_pools  = liq_data["pools"]

    sweep_confirmed = False
    sweep_desc      = "No sweep detected"
    close_back      = False

    if last_sweep:
        # BUY setup: we need a SSL sweep (sell-side liq swept → smart money is bullish)
        if direction == "BUY" and last_sweep.kind == "SSL_sweep":
            sweep_confirmed = True
            close_back      = last_sweep.closed_back
            sweep_desc      = last_sweep.description
        # SELL setup: we need a BSL sweep
        elif direction == "SELL" and last_sweep.kind == "BSL_sweep":
            sweep_confirmed = True
            close_back      = last_sweep.closed_back
            sweep_desc      = last_sweep.description

    if not sweep_confirmed:
        logger.info("🚫 No aligned liquidity sweep — setup incomplete")
        return None

    logger.info(f"💧 Sweep: {sweep_desc}")

    # ── Step 4: MSS / BOS on H1 ───────────────────────────────────────────
    ms_h1          = market_structure.analyse(df_h1)
    mss_confirmed  = False
    structure_desc = "No MSS/BOS"

    # Check for MSS aligned with bias (strongest signal)
    for mss in reversed(ms_h1.mss_events):
        if mss.direction == htf_bias:
            mss_confirmed  = True
            structure_desc = mss.description
            break

    # Fall back to BOS if no MSS
    if not mss_confirmed:
        for bos in reversed(ms_h1.bos_events):
            if bos.direction == htf_bias:
                structure_desc = bos.description
                break

    logger.info(f"🏛  Structure: {structure_desc}")

    # ── Step 5: Point of Interest (OB or FVG) ─────────────────────────────
    current_price = float(df_h1["Close"].iloc[-1])
    atr_h1        = compute_atr(df_h1)

    # Order Blocks on H1
    obs_h1  = order_blocks.detect_order_blocks(df_h1)
    best_ob = order_blocks.nearest_ob(current_price, obs_h1, direction.lower() if direction == "BUY" else "bearish")

    # FVGs on H1
    fvg_direction = "bullish" if direction == "BUY" else "bearish"
    fvgs_h1       = fvg.detect_fvg(df_h1)
    best_fvg      = fvg.nearest_fvg(current_price, fvgs_h1, fvg_direction)

    if best_ob is None and best_fvg is None:
        logger.info("🚫 No OB or FVG found near current price")
        return None

    # Choose POI: prefer OB + FVG overlap (confluence)
    poi_type   = "none"
    poi_top    = None
    poi_bottom = None
    poi_desc   = ""

    if best_ob and best_fvg:
        # Confluence: overlapping OB and FVG
        overlap_top    = min(best_ob.top, best_fvg.top)
        overlap_bottom = max(best_ob.bottom, best_fvg.bottom)
        if overlap_top > overlap_bottom:
            poi_type   = "both"
            poi_top    = overlap_top
            poi_bottom = overlap_bottom
            poi_desc   = (
                f"OB+FVG confluence [{poi_bottom:.5f}–{poi_top:.5f}] "
                f"(OB: {best_ob.formed_at} | FVG: {best_fvg.formed_at})"
            )
        else:
            # No overlap → use OB (stronger institutional signal)
            poi_type   = "OB"
            poi_top    = best_ob.top
            poi_bottom = best_ob.bottom
            poi_desc   = best_ob.description
    elif best_ob:
        poi_type   = "OB"
        poi_top    = best_ob.top
        poi_bottom = best_ob.bottom
        poi_desc   = best_ob.description
    else:
        poi_type   = "FVG"
        poi_top    = best_fvg.top
        poi_bottom = best_fvg.bottom
        poi_desc   = best_fvg.description

    logger.info(f"📍 POI ({poi_type}): {poi_desc}")

    # ── Step 6: 5M Entry confirmation ─────────────────────────────────────
    confirm = _confirm_5m_entry(df_m5, poi_top, poi_bottom, direction)

    if confirm is None:
        logger.info("⏳ No 5M confirmation candle yet — waiting")
        return None

    entry_price = confirm["entry"]
    logger.info(f"✅ 5M confirmation: {confirm['pattern']} at {entry_price:.5f}")

    # ── Step 7: SL / TP / RR ──────────────────────────────────────────────
    swing_highs = ms_h1.swing_highs
    swing_lows  = ms_h1.swing_lows

    sl = _calculate_sl(direction, poi_top, poi_bottom,
                        swing_lows if direction == "BUY" else swing_highs,
                        atr_h1)

    risk_pips   = abs(entry_price - sl) / PIP

    tp = _calculate_tp(
        direction, entry_price,
        swing_highs, swing_lows,
        liq_pools, prev_levels,
        risk_pips
    )

    reward_pips = abs(tp - entry_price) / PIP
    rr_ratio    = round(reward_pips / risk_pips, 2) if risk_pips > 0 else 0

    if rr_ratio < config.MIN_RR_RATIO:
        logger.info(f"🚫 RR {rr_ratio:.1f} below minimum {config.MIN_RR_RATIO} — skip")
        return None

    # ── Confidence score ───────────────────────────────────────────────────
    confidence = _score_setup(
        htf_bias_match  = True,
        sweep_confirmed = sweep_confirmed,
        mss_confirmed   = mss_confirmed,
        in_killzone     = session_status.in_killzone,
        poi_type        = poi_type,
        rr_ratio        = rr_ratio,
        close_back      = close_back,
    )

    # ── Asian range ────────────────────────────────────────────────────────
    asian_r = sessions.asian_range(df_m30 if df_m30 is not None else df_h1)

    # ── Build the setup ────────────────────────────────────────────────────
    setup = TradeSetup(
        pair        = config.PAIR_DISPLAY,
        direction   = direction,
        entry_price = round(entry_price, 5),
        stop_loss   = sl,
        take_profit = tp,
        risk_reward = rr_ratio,
        setup_time  = session_status.local_time_utc3,
        session     = session_status.current_session or "Off-session",
        killzone    = session_status.active_killzone or "None",
        confidence  = confidence,

        htf_bias         = htf_bias.upper(),
        liquidity_swept  = sweep_desc,
        structure_event  = structure_desc,
        point_of_interest= poi_desc,

        reasoning = (
            f"D1 is {htf_bias} → looking for {direction}s. "
            f"Liquidity swept: {sweep_desc}. "
            f"Structure: {structure_desc}. "
            f"POI ({poi_type}): {poi_desc}. "
            f"5M confirmation: {confirm['pattern']}. "
            f"Entry {entry_price:.5f} | SL {sl:.5f} | TP {tp:.5f} | RR {rr_ratio:.1f}."
        ),

        risk_pips          = round(risk_pips, 1),
        reward_pips        = round(reward_pips, 1),
        position_size_lots = _position_size(
            entry_price, sl,
            config.DEFAULT_RISK_PERCENT,
            config.DEFAULT_ACCOUNT_BALANCE
        ),

        asian_range  = asian_r,
        prev_day_high= prev_levels.prev_day_high,
        prev_day_low = prev_levels.prev_day_low,
    )

    logger.info(f"🎯 Setup generated! Confidence: {confidence}/100")
    return setup
