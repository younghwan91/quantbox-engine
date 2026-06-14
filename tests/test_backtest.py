"""Backtest engine: metrics, cost handling, and the no-look-ahead guarantee."""

from __future__ import annotations

import numpy as np

from engine.backtest.vectorized import Trade, run_backtest
from engine.data.klines import synth_ohlcv


def test_trade_net_return_charges_both_legs():
    t = Trade(0, 10, signal=1, entry_price=100.0, exit_price=104.0, reason="tp", cost=0.001)
    assert abs(t.gross_return - 0.04) < 1e-9
    assert abs(t.net_return - (0.04 - 0.002)) < 1e-9  # 2 legs × 0.001


def test_short_gross_return_sign():
    t = Trade(0, 5, signal=-1, entry_price=100.0, exit_price=90.0, reason="tp")
    assert t.gross_return > 0  # short profits when price falls


def test_demo_runs_end_to_end():
    high, low, close = synth_ohlcv(n=1500, seed=3)
    from engine.strategy.demo_squeeze import SqueezeStrategy

    res = run_backtest(SqueezeStrategy(), high, low, close)
    s = res.summary()
    assert s["n_trades"] >= 1
    assert res.equity_curve[0] == 1.0
    assert -1.0 <= s["max_drawdown"] <= 0.0
    assert 0.0 <= s["win_rate"] <= 1.0


def test_no_look_ahead_strategy_only_sees_past():
    """The engine must never hand a strategy data beyond the current bar."""
    high, low, close = synth_ohlcv(n=400, seed=1)
    seen_lengths: list[int] = []

    class Spy:
        _positions: dict = {}

        @property
        def active_positions(self):
            return {}

        def has_position(self, s):
            return False

        def update_market_data(self, s, h, lo, c):
            # record how much history we were shown, and that it ends at "now"
            seen_lengths.append(len(c))
            assert np.array_equal(c, close[: len(c)])  # exact past prefix, no future

        def on_bar(self, s, c):
            return 0

        def get_last_atr_pct(self, s):
            return 0.0

        def open_position(self, *a, **k):
            pass

        def update_position(self, *a, **k):
            return None

        def close_position(self, s):
            return None

    run_backtest(Spy(), high, low, close, warmup=100)
    # Window grows by exactly one bar each step, never jumps ahead.
    assert seen_lengths == list(range(101, 401))
