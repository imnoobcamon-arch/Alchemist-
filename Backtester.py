"""
backtest/backtester.py
──────────────────────
Walk-forward backtester for the ICT strategy.

Methodology:
  • Iterates through historical data window by window
  • At each step, runs the ICT strategy on data UP TO that point
  • Records whether the resulting setup would have hit TP or SL
  • Produces a performance report

⚠️  Note: Backtesting price-action / ICT strategies is inherently limited
because the strategy relies on real-time session context, news, and nuanced
discretionary rules. Treat backtest results as directional, not definitive.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd
import numpy as np

import config
from data.fetcher import fetch_ohlcv

logger = logging.getLogger(__name__)


# ─── Result Dataclass ─────────────────────────────────────────────────────────

@dataclass
class BacktestTrade:
    direction: str
    entry: float
    sl: float
    tp: float
    risk_pips: float
    reward_pips: float
    rr: float
    entry_time: pd.Timestamp
    outcome: str = "open"    # "win" | "loss" | "open"
    exit_price: float = 0.0
    exit_time: Optional[pd.Timestamp] = None
    pnl_pips: float = 0.0
    confidence: int = 0


@dataclass
class BacktestReport:
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_rr: float = 0.0
    total_pnl_pips: float = 0.0
    max_drawdown_pips: float = 0.0
    profit_factor: float = 0.0
    avg_confidence: float = 0.0
    trades: List[BacktestTrade] = field(default_factory=list)


# ─── Outcome Simulation ───────────────────────────────────────────────────────

def _simulate_outcome(
    df_future: pd.DataFrame,
    trade: BacktestTrade
) -> BacktestTrade:
    """
    Walk through future candles to see whether TP or SL is hit first.

    Uses High/Low of each candle to detect which level was touched.
    Conservative assumption: if both hit on the same candle, SL wins.
    """
    for ts, row in df_future.iterrows():
        if trade.direction == "BUY":
            sl_hit = row["Low"]  <= trade.sl
            tp_hit = row["High"] >= trade.tp
        else:
            sl_hit = row["High"] >= trade.sl
            tp_hit = row["Low"]  <= trade.tp

        if sl_hit and tp_hit:
            # Conservative: SL hit first
            trade.outcome    = "loss"
            trade.exit_price = trade.sl
            trade.exit_time  = ts
            trade.pnl_pips   = -trade.risk_pips
            break
        elif tp_hit:
            trade.outcome    = "win"
            trade.exit_price = trade.tp
            trade.exit_time  = ts
            trade.pnl_pips   = trade.reward_pips
            break
        elif sl_hit:
            trade.outcome    = "loss"
            trade.exit_price = trade.sl
            trade.exit_time  = ts
            trade.pnl_pips   = -trade.risk_pips
            break

    return trade


# ─── Walk-Forward Engine ──────────────────────────────────────────────────────

def run_backtest(
    window_size: int = 200,
    step_size:   int = 24,
    max_trades:  int = 50
) -> BacktestReport:
    """
    Walk-forward backtest over H1 data.

    Parameters
    ----------
    window_size : candles in the analysis window
    step_size   : candles to step forward each iteration
    max_trades  : maximum number of trades to simulate

    Returns
    -------
    BacktestReport
    """
    logger.info("🔄 Starting backtest …")
    logger.info(f"   Window: {window_size} candles | Step: {step_size} | Max trades: {max_trades}")

    # Fetch raw data
    df_h1_full = fetch_ohlcv("H1", n_candles=window_size + max_trades * step_size + 100)
    df_d1_full = fetch_ohlcv("D1", n_candles=300)
    df_m5_full = fetch_ohlcv("M5", n_candles=500)

    if df_h1_full is None or df_h1_full.empty:
        logger.error("No H1 data available for backtest")
        return BacktestReport()

    report = BacktestReport()
    n      = len(df_h1_full)

    for start in range(0, n - window_size - step_size, step_size):
        if len(report.trades) >= max_trades:
            break

        end      = start + window_size
        df_slice = df_h1_full.iloc[start:end]
        df_future= df_h1_full.iloc[end: end + step_size * 5]

        if df_future.empty:
            break

        # Run strategy on the sliced data
        try:
            data_slice = {
                "D1":  df_d1_full,
                "H4":  df_slice,
                "H1":  df_slice,
                "M30": df_slice,
                "M5":  df_m5_full,
            }

            from strategies.ict_strategy import analyse
            setup = analyse(data_slice)
        except Exception as e:
            logger.debug(f"Strategy error at step {start}: {e}")
            continue

        if setup is None:
            continue

        # Create a backtest trade
        trade = BacktestTrade(
            direction   = setup.direction,
            entry       = setup.entry_price,
            sl          = setup.stop_loss,
            tp          = setup.take_profit,
            risk_pips   = setup.risk_pips,
            reward_pips = setup.reward_pips,
            rr          = setup.risk_reward,
            entry_time  = df_slice.index[-1],
            confidence  = setup.confidence,
        )

        trade = _simulate_outcome(df_future, trade)
        report.trades.append(trade)

        outcome_str = "✅ WIN" if trade.outcome == "win" else "❌ LOSS" if trade.outcome == "loss" else "⏳ OPEN"
        logger.info(
            f"Trade {len(report.trades):02d} | {trade.direction} @ {trade.entry:.5f} "
            f"| RR {trade.rr:.1f} | {outcome_str} ({trade.pnl_pips:+.1f} pips)"
        )

    # ── Compute metrics ──────────────────────────────────────────────────────
    closed = [t for t in report.trades if t.outcome != "open"]

    report.total_trades = len(closed)
    report.wins         = sum(1 for t in closed if t.outcome == "win")
    report.losses       = report.total_trades - report.wins
    report.win_rate     = (report.wins / report.total_trades * 100) if report.total_trades else 0
    report.avg_rr       = float(np.mean([t.rr for t in closed])) if closed else 0
    report.total_pnl_pips = sum(t.pnl_pips for t in closed)
    report.avg_confidence = float(np.mean([t.confidence for t in report.trades])) if report.trades else 0

    # Profit factor
    gross_profit = sum(t.pnl_pips for t in closed if t.pnl_pips > 0)
    gross_loss   = abs(sum(t.pnl_pips for t in closed if t.pnl_pips < 0))
    report.profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

    # Max drawdown
    running_pnl = 0.0
    peak        = 0.0
    max_dd      = 0.0
    for t in closed:
        running_pnl += t.pnl_pips
        peak = max(peak, running_pnl)
        dd   = peak - running_pnl
        max_dd = max(max_dd, dd)
    report.max_drawdown_pips = max_dd

    return report


# ─── Report Printer ───────────────────────────────────────────────────────────

def print_report(report: BacktestReport) -> None:
    """Display a formatted backtest summary."""
    pf_str = f"{report.profit_factor:.2f}" if report.profit_factor != float("inf") else "∞"
    print("\n" + "═" * 48)
    print("  📊  BACKTEST RESULTS — ICT Strategy (EURUSD)")
    print("═" * 48)
    print(f"  Total Trades      : {report.total_trades}")
    print(f"  Wins              : {report.wins}")
    print(f"  Losses            : {report.losses}")
    print(f"  Win Rate          : {report.win_rate:.1f}%")
    print(f"  Average RR        : {report.avg_rr:.2f}")
    print(f"  Total PnL (pips)  : {report.total_pnl_pips:+.1f}")
    print(f"  Profit Factor     : {pf_str}")
    print(f"  Max Drawdown(pips): {report.max_drawdown_pips:.1f}")
    print(f"  Avg Confidence    : {report.avg_confidence:.0f}/100")
    print("═" * 48)

    if report.trades:
        print("\n  Trade Log:")
        print(f"  {'#':>3}  {'Dir':<5}  {'Entry':>9}  {'RR':>5}  {'PnL':>8}  Outcome")
        print(f"  {'─'*3}  {'─'*5}  {'─'*9}  {'─'*5}  {'─'*8}  {'─'*7}")
        for i, t in enumerate(report.trades, 1):
            outcome = "WIN" if t.outcome == "win" else "LOSS" if t.outcome == "loss" else "OPEN"
            print(
                f"  {i:>3}  {t.direction:<5}  {t.entry:>9.5f}  {t.rr:>5.1f}  "
                f"{t.pnl_pips:>+8.1f}  {outcome}"
            )
    print()
