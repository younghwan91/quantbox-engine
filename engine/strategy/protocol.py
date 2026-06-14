"""Strategy protocol — the formal contract every strategy must satisfy.

The engine (backtester and live scalper alike) only ever talks to a strategy
through this interface, never to a concrete class.  That decoupling is what lets
the *same* engine run any strategy: a strategy is just an object that ingests
bars, emits signals, and tracks its own open positions and exit conditions.

Implemented via :pep:`544` structural subtyping — a strategy needs no base
class, only these methods.  ``@runtime_checkable`` allows ``isinstance`` checks
in tests.  The bundled :class:`~engine.strategy.demo_squeeze.SqueezeStrategy`
is a reference implementation on public, textbook logic.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class TradingStrategy(Protocol):
    """Interface for a stateful trading strategy.

    Signal convention (returned by :meth:`on_bar`):
        ``-1`` short · ``0`` flat/no-signal · ``1`` long.

    Per-bar flow driven by the engine:
        1. :meth:`update_market_data` — feed the latest OHLCV window.
        2. :meth:`on_bar` — emit a signal for a symbol.
        3. :meth:`open_position` / :meth:`update_position` — lifecycle.
    """

    # ── position book ────────────────────────────────────────────────

    @property
    def active_positions(self) -> dict[str, dict]:
        """Snapshot of open positions keyed by symbol."""
        ...

    def has_position(self, symbol: str) -> bool:
        """Whether a position is currently open for *symbol*."""
        ...

    # ── data feed ────────────────────────────────────────────────────

    def update_market_data(
        self,
        symbol: str,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
    ) -> None:
        """Store the latest OHLC arrays for *symbol* before :meth:`on_bar`."""
        ...

    # ── signal generation ────────────────────────────────────────────

    def on_bar(self, symbol: str, close: np.ndarray) -> int:
        """Return a signal (``-1`` / ``0`` / ``1``) for the latest bar."""
        ...

    def get_last_atr_pct(self, symbol: str) -> float:
        """Return the most recent ATR as a fraction of price (for sizing)."""
        ...

    # ── position lifecycle ───────────────────────────────────────────

    def open_position(
        self,
        symbol: str,
        signal: int,
        entry_price: float,
        atr_pct: float,
    ) -> None:
        """Record a newly opened position."""
        ...

    def update_position(
        self,
        symbol: str,
        price: float,
        high: float | None = None,
        low: float | None = None,
    ) -> str | None:
        """Evaluate exit conditions for an open position.

        Returns an exit-reason string (e.g. ``"stop_loss"``, ``"take_profit"``,
        ``"trailing_stop"``) or ``None`` to keep the position open.
        """
        ...

    def close_position(self, symbol: str) -> dict | None:
        """Remove and return the position record, or ``None`` if absent."""
        ...
