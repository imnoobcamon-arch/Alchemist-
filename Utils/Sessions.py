"""
utils/sessions.py
─────────────────
Session and Kill-Zone management (UTC+3 timezone).

ICT Kill Zones are periods of high institutional activity where the best
trade setups occur:
  • London Open Kill Zone  : 10:00–12:00 UTC+3
  • New York Open Kill Zone: 15:00–17:00 UTC+3
  • New York Close         : 20:00–22:00 UTC+3
"""

import logging
from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional, Tuple

import pytz

import config

logger = logging.getLogger(__name__)

# UTC+3 timezone object
TZ_UTC3 = pytz.timezone("Etc/GMT-3")   # GMT-3 = UTC+3 in pytz naming


@dataclass
class SessionStatus:
    current_session: Optional[str]       # "Asian" | "London" | "NewYork" | None
    active_killzone: Optional[str]       # "London_Open" | "NewYork_Open" | etc.
    in_killzone: bool
    local_time_utc3: str                 # human-readable UTC+3 time
    is_weekend: bool


def _parse_time(t_str: str) -> time:
    """Parse "HH:MM" into a time object."""
    h, m = t_str.split(":")
    return time(int(h), int(m))


def _time_in_range(t: time, start_str: str, end_str: str) -> bool:
    """Return True if time `t` falls within [start, end)."""
    start = _parse_time(start_str)
    end   = _parse_time(end_str)
    if start <= end:
        return start <= t < end
    # Overnight range
    return t >= start or t < end


def get_session_status(dt_utc: datetime = None) -> SessionStatus:
    """
    Determine the current session and kill-zone status.

    Parameters
    ----------
    dt_utc : datetime in UTC (defaults to now)

    Returns
    -------
    SessionStatus
    """
    if dt_utc is None:
        dt_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
    elif dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=pytz.utc)

    dt_utc3 = dt_utc.astimezone(TZ_UTC3)
    local_t = dt_utc3.time()

    is_weekend = dt_utc3.weekday() >= 5   # Saturday=5, Sunday=6

    # ── Current session ────────────────────────────────────────────────────
    current_session = None
    for sess_name, window in config.SESSIONS.items():
        if _time_in_range(local_t, window["start"], window["end"]):
            current_session = sess_name
            break

    # ── Active kill zone ───────────────────────────────────────────────────
    active_killzone = None
    for kz_name, window in config.KILLZONES.items():
        if _time_in_range(local_t, window["start"], window["end"]):
            active_killzone = kz_name
            break

    return SessionStatus(
        current_session  = current_session,
        active_killzone  = active_killzone,
        in_killzone      = active_killzone is not None,
        local_time_utc3  = dt_utc3.strftime("%Y-%m-%d %H:%M UTC+3"),
        is_weekend       = is_weekend,
    )


def is_killzone_candle(timestamp: datetime) -> bool:
    """Return True if `timestamp` falls within any kill zone."""
    status = get_session_status(timestamp)
    return status.in_killzone


def filter_killzone_candles(df) -> 'pd.DataFrame':
    """
    Filter a DataFrame to retain only candles that fall within kill zones.
    Useful for 5M entry confirmation.
    """
    import pandas as pd
    df = df.copy()
    df.index = pd.to_datetime(df.index, utc=True)
    mask = df.index.map(lambda ts: is_killzone_candle(ts.to_pydatetime()))
    return df[mask]


def session_range(df, session_name: str) -> Tuple[Optional[float], Optional[float]]:
    """
    Compute the High and Low of a given session from an intraday DataFrame.

    Parameters
    ----------
    df           : intraday OHLCV (e.g., 5M or 30M)
    session_name : "Asian" | "London" | "NewYork"

    Returns
    -------
    (session_high, session_low) or (None, None) if session not yet formed
    """
    import pandas as pd

    window = config.SESSIONS.get(session_name)
    if window is None:
        return None, None

    df_copy = df.copy()
    df_copy.index = pd.to_datetime(df_copy.index, utc=True)

    # Convert index to UTC+3
    df_copy.index = df_copy.index.tz_convert(TZ_UTC3)
    start_t = _parse_time(window["start"])
    end_t   = _parse_time(window["end"])

    mask = df_copy.index.map(
        lambda ts: _time_in_range(ts.time(), window["start"], window["end"])
    )
    session_data = df_copy[mask]

    if session_data.empty:
        return None, None

    return float(session_data["High"].max()), float(session_data["Low"].min())


def asian_range(df) -> dict:
    """
    Return the Asian session range dict.
    The Asian range defines a tight consolidation zone; London/NY sessions
    often expand from this range to take liquidity on both sides.
    """
    high, low = session_range(df, "Asian")
    if high is None:
        return {"high": None, "low": None, "mid": None, "formed": False}

    return {
        "high":   high,
        "low":    low,
        "mid":    (high + low) / 2,
        "size_pips": (high - low) / 0.0001,
        "formed": True,
    }


def log_session_info(status: SessionStatus) -> None:
    """Log current session status."""
    session_str   = status.current_session or "Between sessions"
    killzone_str  = status.active_killzone or "None"
    weekend_str   = " ⚠️  WEEKEND — market closed" if status.is_weekend else ""
    logger.info(
        f"📅 {status.local_time_utc3} | "
        f"Session: {session_str} | "
        f"Kill Zone: {killzone_str}"
        f"{weekend_str}"
    )
