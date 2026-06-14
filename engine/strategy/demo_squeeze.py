"""Demo strategy — Bollinger Band squeeze mean-reversion.

A deliberately simple, **public-domain** strategy that exists to exercise the
engine end to end, not to make money.  It implements the
:class:`~engine.strategy.protocol.TradingStrategy` contract so the backtester
and the live scalper can both drive it without modification.

Idea (textbook):
    * A *squeeze* is a low-volatility regime — Bollinger Band width sits in the
      bottom decile of its recent range.  Such compression often precedes
      expansion.
    * During a squeeze we fade extremes: a close below the lower band is a long
      (mean-reversion up), a close above the upper band is a short.
    * Each position carries a fixed stop-loss, take-profit, and an optional
      trailing stop once it moves into profit.

Discipline:
    Every indicator reads only *completed* bars (the arrays passed in exclude
    the in-progress candle).  No future data touches a signal — see
    :mod:`engine.backtest.vectorized` for how the engine enforces this.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class SqueezeConfig:
    """Strategy parameters (transparent, public defaults)."""

    bb_period: int = 20            # Bollinger Band lookback
    bb_std: float = 2.0           # band width in standard deviations
    squeeze_lookback: int = 100    # window for the band-width percentile
    squeeze_pct: float = 0.25      # "squeeze" if width <= this percentile
    atr_period: int = 14
    stop_loss: float = 0.02        # 2% hard stop
    take_profit: float = 0.04      # 4% target
    trail_distance: float = 0.01   # trail 1% behind peak once armed
    trail_activate: float = 0.02   # arm the trail at +2% unrealised


def _sma(x: np.ndarray, n: int) -> float:
    return float(np.mean(x[-n:]))


def _atr_pct(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int) -> float:
    """ATR as a fraction of the last close (Wilder true range, simple mean)."""
    if len(close) < n + 1:
        return 0.0
    tr = np.maximum.reduce([
        high[-n:] - low[-n:],
        np.abs(high[-n:] - close[-n - 1 : -1]),
        np.abs(low[-n:] - close[-n - 1 : -1]),
    ])
    last = close[-1]
    return float(np.mean(tr) / last) if last else 0.0


class SqueezeStrategy:
    """Reference implementation of :class:`TradingStrategy`."""

    def __init__(self, config: SqueezeConfig | None = None) -> None:
        self.config = config or SqueezeConfig()
        self._positions: dict[str, dict] = {}
        self._highs: dict[str, np.ndarray] = {}
        self._lows: dict[str, np.ndarray] = {}
        self._atr_pct: dict[str, float] = {}

    # ── position book ────────────────────────────────────────────────

    @property
    def active_positions(self) -> dict[str, dict]:
        return {k: dict(v) for k, v in self._positions.items()}

    def has_position(self, symbol: str) -> bool:
        return symbol in self._positions

    # ── data feed ────────────────────────────────────────────────────

    def update_market_data(
        self, symbol: str, high: np.ndarray, low: np.ndarray, close: np.ndarray
    ) -> None:
        self._highs[symbol] = np.asarray(high, dtype=float)
        self._lows[symbol] = np.asarray(low, dtype=float)
        self._atr_pct[symbol] = _atr_pct(
            self._highs[symbol], self._lows[symbol],
            np.asarray(close, dtype=float), self.config.atr_period,
        )

    # ── signal ───────────────────────────────────────────────────────

    def _band_width(self, close: np.ndarray) -> np.ndarray:
        """Rolling Bollinger band width (upper-lower) / mid, per bar."""
        c = self.config
        n = c.bb_period
        widths = np.full(len(close), np.nan)
        for i in range(n, len(close) + 1):
            window = close[i - n : i]
            mid = window.mean()
            sd = window.std()
            if mid:
                widths[i - 1] = (2 * c.bb_std * sd) / mid
        return widths

    def on_bar(self, symbol: str, close: np.ndarray) -> int:
        c = self.config
        close = np.asarray(close, dtype=float)
        if len(close) < max(c.bb_period, c.squeeze_lookback):
            return 0

        widths = self._band_width(close)
        cur_width = widths[-1]
        ref = widths[-c.squeeze_lookback :]
        ref = ref[~np.isnan(ref)]
        if np.isnan(cur_width) or len(ref) < c.squeeze_lookback // 2:
            return 0

        # In a squeeze? (band width in the bottom `squeeze_pct` of recent range)
        threshold = np.quantile(ref, c.squeeze_pct)
        if cur_width > threshold:
            return 0

        mid = _sma(close, c.bb_period)
        sd = float(close[-c.bb_period :].std())
        upper, lower = mid + c.bb_std * sd, mid - c.bb_std * sd
        price = close[-1]
        if price < lower:
            return 1   # fade the downside extreme
        if price > upper:
            return -1  # fade the upside extreme
        return 0

    def get_last_atr_pct(self, symbol: str) -> float:
        return self._atr_pct.get(symbol, 0.0)

    # ── lifecycle ────────────────────────────────────────────────────

    def open_position(
        self, symbol: str, signal: int, entry_price: float, atr_pct: float
    ) -> None:
        self._positions[symbol] = {
            "signal": signal,
            "entry_price": entry_price,
            "atr_pct": atr_pct,
            "peak_gain": 0.0,
            "trail_active": False,
        }

    def update_position(
        self,
        symbol: str,
        price: float,
        high: float | None = None,
        low: float | None = None,
    ) -> str | None:
        pos = self._positions.get(symbol)
        if pos is None:
            return None
        c = self.config
        direction = pos["signal"]
        entry = pos["entry_price"]
        # Unrealised gain in the position's favour.
        gain = direction * (price - entry) / entry

        if gain <= -c.stop_loss:
            return "stop_loss"
        if gain >= c.take_profit:
            return "take_profit"

        # Trailing stop: arm at +trail_activate, then exit if we give back
        # trail_distance from the peak.
        pos["peak_gain"] = max(pos["peak_gain"], gain)
        if pos["peak_gain"] >= c.trail_activate:
            pos["trail_active"] = True
        if pos["trail_active"] and gain <= pos["peak_gain"] - c.trail_distance:
            return "trailing_stop"
        return None

    def close_position(self, symbol: str) -> dict | None:
        return self._positions.pop(symbol, None)
