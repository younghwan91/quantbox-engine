"""Event-stepped backtest engine with a strict no-look-ahead guarantee.

The single most common way a backtest lies is by letting a signal peek at data
that would not yet exist in live trading.  This engine makes that structurally
impossible: at bar ``t`` the strategy is handed only ``close[: t + 1]`` — the
slice of *completed* bars up to and including ``t`` — and the resulting signal
can only act from bar ``t`` onward.  The same strategy object that runs here is
the one the live scalper drives, so backtest and live share one code path.

Costs (taker fee + slippage) are charged on entry and exit so headline numbers
are net, not gross.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field

import numpy as np

from engine.strategy.protocol import TradingStrategy


@dataclass
class Trade:
    entry_idx: int
    exit_idx: int
    signal: int
    entry_price: float
    exit_price: float
    reason: str
    cost: float = 0.0006  # per-leg cost (fee + slippage) as a fraction

    @property
    def gross_return(self) -> float:
        return self.signal * (self.exit_price - self.entry_price) / self.entry_price

    @property
    def net_return(self) -> float:
        """Return after charging ``cost`` on both entry and exit legs."""
        return self.gross_return - 2 * self.cost


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    equity_curve: np.ndarray = field(default_factory=lambda: np.array([1.0]))

    @property
    def n_trades(self) -> int:
        return len(self.trades)

    @property
    def total_return(self) -> float:
        return float(self.equity_curve[-1] - 1.0)

    @property
    def win_rate(self) -> float:
        if not self.trades:
            return 0.0
        wins = sum(1 for t in self.trades if t.net_return > 0)
        return wins / len(self.trades)

    @property
    def profit_factor(self) -> float:
        gains = sum(t.net_return for t in self.trades if t.net_return > 0)
        losses = -sum(t.net_return for t in self.trades if t.net_return < 0)
        if losses == 0:
            return float("inf") if gains > 0 else 0.0
        return gains / losses

    @property
    def max_drawdown(self) -> float:
        peak = np.maximum.accumulate(self.equity_curve)
        return float(np.min(self.equity_curve / peak - 1.0))

    def summary(self) -> dict[str, float]:
        return {
            "n_trades": self.n_trades,
            "total_return": self.total_return,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "max_drawdown": self.max_drawdown,
        }


def run_backtest(
    strategy: TradingStrategy,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    symbol: str = "DEMO",
    fee: float = 0.0004,       # 4 bps taker
    slippage: float = 0.0002,  # 2 bps
    warmup: int = 100,
) -> BacktestResult:
    """Step a strategy bar-by-bar over one OHLC series; return performance.

    At each bar the strategy sees only completed history (``[: t + 1]``).  A
    flat strategy may open on the bar that produced the signal; an open position
    is checked for exits each subsequent bar.  Returns are net of ``fee`` and
    ``slippage`` charged on both legs.
    """
    high = np.asarray(high, float)
    low = np.asarray(low, float)
    close = np.asarray(close, float)
    n = len(close)

    equity = 1.0
    curve = [equity]
    trades: list[Trade] = []
    open_trade: dict | None = None
    cost = fee + slippage  # per-leg cost as a fraction

    for t in range(warmup, n):
        # ── no-look-ahead: strategy only ever sees bars up to t ──
        h, lo, c = high[: t + 1], low[: t + 1], close[: t + 1]
        strategy.update_market_data(symbol, h, lo, c)
        price = close[t]

        if open_trade is None:
            signal = strategy.on_bar(symbol, c)
            if signal != 0:
                atr_pct = strategy.get_last_atr_pct(symbol)
                strategy.open_position(symbol, signal, price, atr_pct)
                open_trade = {"entry_idx": t, "entry_price": price, "signal": signal}
        else:
            reason = strategy.update_position(symbol, price, high[t], low[t])
            if reason is not None:
                strategy.close_position(symbol)
                tr = Trade(
                    entry_idx=open_trade["entry_idx"],
                    exit_idx=t,
                    signal=open_trade["signal"],
                    entry_price=open_trade["entry_price"],
                    exit_price=price,
                    reason=reason,
                    cost=cost,
                )
                trades.append(tr)
                equity *= 1.0 + tr.net_return
                open_trade = None
        curve.append(equity)

    return BacktestResult(trades=trades, equity_curve=np.array(curve))


def _demo() -> int:
    from engine.data.klines import synth_ohlcv

    high, low, close = synth_ohlcv(n=2000, seed=7)
    from engine.strategy.demo_squeeze import SqueezeStrategy

    result = run_backtest(SqueezeStrategy(), high, low, close)
    s = result.summary()
    print("QuantBox Engine — demo backtest (Bollinger squeeze, synthetic data)")
    print(f"  trades        : {s['n_trades']}")
    print(f"  total return  : {s['total_return']:+.2%}")
    print(f"  win rate      : {s['win_rate']:.1%}")
    print(f"  profit factor : {s['profit_factor']:.2f}")
    print(f"  max drawdown  : {s['max_drawdown']:.2%}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="QuantBox Engine backtest")
    parser.add_argument("--demo", action="store_true", help="run the bundled demo")
    args = parser.parse_args()
    if args.demo:
        return _demo()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
