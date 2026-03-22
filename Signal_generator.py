"""
signals/signal_generator.py
────────────────────────────
Formats TradeSetup objects into human-readable signal cards,
saves them to the journal, and optionally sends Telegram alerts.
"""

import csv
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import config
from strategies.ict_strategy import TradeSetup

logger = logging.getLogger(__name__)


# ─── Signal Formatter ─────────────────────────────────────────────────────────

DIRECTION_EMOJI = {"BUY": "📈", "SELL": "📉"}
CONFIDENCE_BAR  = {
    range(0,  50): "🔴 Low",
    range(50, 70): "🟡 Moderate",
    range(70, 85): "🟢 High",
    range(85,101): "⭐ Elite",
}

def _confidence_label(score: int) -> str:
    for rng, label in CONFIDENCE_BAR.items():
        if score in rng:
            return label
    return "Unknown"


def format_signal(setup: TradeSetup) -> str:
    """
    Render a trade setup as a clean, readable signal card.

    Returns a multi-line string suitable for console output or Telegram.
    """
    emoji     = DIRECTION_EMOJI.get(setup.direction, "📊")
    conf_str  = _confidence_label(setup.confidence)

    lines = [
        "╔══════════════════════════════════════════╗",
        f"║  {emoji}  ICT TRADE SIGNAL — {setup.pair:<17}║",
        "╠══════════════════════════════════════════╣",
        f"║  Direction    : {setup.direction:<25}║",
        f"║  Entry Price  : {setup.entry_price:<25.5f}║",
        f"║  Stop Loss    : {setup.stop_loss:<25.5f}║",
        f"║  Take Profit  : {setup.take_profit:<25.5f}║",
        f"║  Risk : Reward: 1 : {setup.risk_reward:<21.1f}║",
        f"║  Risk Pips    : {setup.risk_pips:<25.1f}║",
        f"║  Reward Pips  : {setup.reward_pips:<25.1f}║",
        f"║  Lot Size     : {setup.position_size_lots:<25.2f}║",
        "╠══════════════════════════════════════════╣",
        f"║  Confidence   : {conf_str:<25}║",
        f"║  Session      : {setup.session:<25}║",
        f"║  Kill Zone    : {setup.killzone:<25}║",
        f"║  Time (UTC+3) : {setup.setup_time:<25}║",
        "╠══════════════════════════════════════════╣",
        "║  ── ICT Analysis ──                      ║",
        f"║  HTF Bias     : {setup.htf_bias:<25}║",
    ]

    # Wrap long strings
    def _wrap_field(label: str, value: str, width: int = 25) -> list:
        words = value.split()
        row   = ""
        rows  = []
        for w in words:
            if len(row) + len(w) + 1 <= width:
                row += ("" if not row else " ") + w
            else:
                if row:
                    rows.append(row)
                row = w
        if row:
            rows.append(row)

        result = []
        for j, r in enumerate(rows):
            lbl = label if j == 0 else " " * len(label)
            result.append(f"║  {lbl}: {r:<{width}}║")
        return result

    sweep_short = (setup.liquidity_swept[:60] + "…") if len(setup.liquidity_swept) > 60 else setup.liquidity_swept
    struct_short= (setup.structure_event[:60] + "…") if len(setup.structure_event) > 60 else setup.structure_event
    poi_short   = (setup.point_of_interest[:60] + "…") if len(setup.point_of_interest) > 60 else setup.point_of_interest

    lines.append(f"║  Liq Sweep    : {sweep_short[:25]:<25}║")
    lines.append(f"║  Structure    : {struct_short[:25]:<25}║")
    lines.append(f"║  POI          : {poi_short[:25]:<25}║")

    if setup.prev_day_high:
        lines.append(f"║  Prev Day H   : {setup.prev_day_high:<25.5f}║")
    if setup.prev_day_low:
        lines.append(f"║  Prev Day L   : {setup.prev_day_low:<25.5f}║")

    if setup.asian_range.get("formed"):
        ar = setup.asian_range
        lines.append(f"║  Asian Hi     : {ar['high']:<25.5f}║")
        lines.append(f"║  Asian Lo     : {ar['low']:<25.5f}║")

    lines.append("╚══════════════════════════════════════════╝")

    return "\n".join(lines)


def format_reasoning(setup: TradeSetup) -> str:
    """Return the detailed ICT reasoning paragraph."""
    return (
        "\n📝 REASONING\n"
        "─────────────────────────────────────────────────────────────\n"
        f"{setup.reasoning}\n"
        "─────────────────────────────────────────────────────────────\n"
    )


# ─── Journal ──────────────────────────────────────────────────────────────────

JOURNAL_HEADERS = [
    "date", "time_utc3", "pair", "direction",
    "entry", "stop_loss", "take_profit", "rr",
    "risk_pips", "reward_pips", "lots",
    "confidence", "session", "killzone",
    "htf_bias", "poi_type", "reasoning",
    "outcome", "pnl_pips", "notes"
]


def save_to_journal(setup: TradeSetup) -> None:
    """Append the trade setup to the CSV journal."""
    journal_path = Path(config.JOURNAL_FILE)
    journal_path.parent.mkdir(parents=True, exist_ok=True)

    write_header = not journal_path.exists()

    ts = datetime.utcnow()

    row = {
        "date"       : ts.strftime("%Y-%m-%d"),
        "time_utc3"  : setup.setup_time,
        "pair"       : setup.pair,
        "direction"  : setup.direction,
        "entry"      : setup.entry_price,
        "stop_loss"  : setup.stop_loss,
        "take_profit": setup.take_profit,
        "rr"         : setup.risk_reward,
        "risk_pips"  : setup.risk_pips,
        "reward_pips": setup.reward_pips,
        "lots"       : setup.position_size_lots,
        "confidence" : setup.confidence,
        "session"    : setup.session,
        "killzone"   : setup.killzone,
        "htf_bias"   : setup.htf_bias,
        "poi_type"   : setup.point_of_interest[:80],
        "reasoning"  : setup.reasoning[:200],
        "outcome"    : "",   # to be filled manually
        "pnl_pips"   : "",
        "notes"      : "",
    }

    with open(journal_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=JOURNAL_HEADERS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)

    logger.info(f"📓 Signal saved to journal: {journal_path}")


def save_signal_json(setup: TradeSetup) -> str:
    """Save signal as JSON in the signals/ directory."""
    signals_dir = Path("signals")
    signals_dir.mkdir(exist_ok=True)

    ts_str = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = signals_dir / f"signal_{setup.pair}_{setup.direction}_{ts_str}.json"

    payload = {
        "pair"         : setup.pair,
        "direction"    : setup.direction,
        "entry_price"  : setup.entry_price,
        "stop_loss"    : setup.stop_loss,
        "take_profit"  : setup.take_profit,
        "risk_reward"  : setup.risk_reward,
        "risk_pips"    : setup.risk_pips,
        "reward_pips"  : setup.reward_pips,
        "lots"         : setup.position_size_lots,
        "confidence"   : setup.confidence,
        "session"      : setup.session,
        "killzone"     : setup.killzone,
        "setup_time"   : setup.setup_time,
        "htf_bias"     : setup.htf_bias,
        "liquidity_swept"    : setup.liquidity_swept,
        "structure_event"    : setup.structure_event,
        "point_of_interest"  : setup.point_of_interest,
        "reasoning"          : setup.reasoning,
        "prev_day_high"      : setup.prev_day_high,
        "prev_day_low"       : setup.prev_day_low,
        "asian_range"        : setup.asian_range,
    }

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)

    logger.info(f"💾 Signal JSON saved: {filename}")
    return str(filename)


# ─── Telegram ─────────────────────────────────────────────────────────────────

def send_telegram(setup: TradeSetup) -> None:
    """
    Send the signal card to a Telegram chat via the Bot API.
    Requires config.TELEGRAM_ENABLED = True and valid token/chat_id.
    """
    if not config.TELEGRAM_ENABLED:
        return

    try:
        import requests
        url  = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
        text = format_signal(setup) + "\n" + format_reasoning(setup)

        resp = requests.post(url, json={
            "chat_id"   : config.TELEGRAM_CHAT_ID,
            "text"      : f"```\n{text}\n```",
            "parse_mode": "Markdown"
        }, timeout=10)

        if resp.status_code == 200:
            logger.info("✈️  Signal sent to Telegram")
        else:
            logger.warning(f"Telegram error: {resp.status_code} — {resp.text[:200]}")
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")


# ─── Main publish function ────────────────────────────────────────────────────

def publish(setup: Optional[TradeSetup]) -> None:
    """
    Main entry point — format, display, save, and optionally send the setup.
    """
    if setup is None:
        logger.info("No valid setup to publish.")
        print("\n⚠️  No trade setup generated — conditions not met.\n")
        return

    card      = format_signal(setup)
    reasoning = format_reasoning(setup)

    # Console output
    print("\n" + card)
    print(reasoning)

    # Persist
    save_to_journal(setup)
    save_signal_json(setup)

    # Telegram
    send_telegram(setup)
