"""SqueezeStrategy: protocol conformance, signals, and exit logic."""

from __future__ import annotations

import numpy as np

from engine.strategy.demo_squeeze import SqueezeConfig, SqueezeStrategy
from engine.strategy.protocol import TradingStrategy


def test_satisfies_protocol():
    assert isinstance(SqueezeStrategy(), TradingStrategy)


def test_no_signal_before_warmup():
    s = SqueezeStrategy()
    assert s.on_bar("X", np.linspace(100, 101, 30)) == 0


def test_position_lifecycle_and_stop_loss():
    s = SqueezeStrategy(SqueezeConfig(stop_loss=0.02, take_profit=0.04))
    s.open_position("X", signal=1, entry_price=100.0, atr_pct=0.01)
    assert s.has_position("X")
    assert s.update_position("X", 99.5) is None        # -0.5%, holds
    assert s.update_position("X", 97.9) == "stop_loss"  # -2.1%, stops out
    closed = s.close_position("X")
    assert closed is not None and not s.has_position("X")


def test_take_profit_and_trailing():
    s = SqueezeStrategy(SqueezeConfig(take_profit=0.04, trail_activate=0.02, trail_distance=0.01))
    s.open_position("X", signal=1, entry_price=100.0, atr_pct=0.01)
    assert s.update_position("X", 103.0) is None        # +3%, trail armed, holds
    assert s.update_position("X", 101.9) == "trailing_stop"  # gave back >1% from +3% peak


def test_short_side_stop():
    s = SqueezeStrategy(SqueezeConfig(stop_loss=0.02))
    s.open_position("X", signal=-1, entry_price=100.0, atr_pct=0.01)
    assert s.update_position("X", 102.5) == "stop_loss"  # price up 2.5% hurts a short


def test_atr_pct_is_a_fraction():
    s = SqueezeStrategy()
    n = 200
    high = np.full(n, 101.0)
    low = np.full(n, 99.0)
    close = np.full(n, 100.0)
    s.update_market_data("X", high, low, close)
    atr = s.get_last_atr_pct("X")
    assert 0.0 < atr < 0.1  # ~2% range / 100
