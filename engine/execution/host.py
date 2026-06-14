"""Host interface for execution mixins.

The live trading bot is composed from mixins (``BracketMixin`` etc.) that are
mixed into a single ``Scalper`` class.  Each mixin needs to call methods that
live on *sibling* mixins or on the composed host.  To type-check a mixin in
isolation — without importing the concrete (and strategy-specific) host class —
we declare the surface it relies on as a ``Protocol``.

This is the strategy-agnostic slice of that interface: only the order-placement
and bookkeeping members the bracket manager actually touches.  Concrete trailing
parameters come from :class:`TrailConfig` rather than any proprietary strategy
config, so the bracket logic is fully decoupled from alpha.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # pragma: no cover
    from engine.strategy.protocol import TradingStrategy


@dataclass(frozen=True)
class TrailConfig:
    """Trailing-stop geometry, expressed as fractions of entry price.

    Attributes:
        trail_distance: Callback distance the stop trails behind the peak
            (e.g. ``0.005`` = 0.5%).  Binance requires callbackRate >= 0.5%.
        trail_activate: Unrealised gain at which the trailing stop arms
            (e.g. ``0.02`` = +2%).
    """

    trail_distance: float = 0.005
    trail_activate: float = 0.02


class ScalperProtocol(Protocol):
    """Host surface used by :class:`BracketMixin` (strategy-agnostic)."""

    # ── state ────────────────────────────────────────────────────────
    paper_mode: bool
    client: Any
    discord: Any
    strategy: "TradingStrategy"
    trail_config: TrailConfig
    _algo_api: Any

    # ── exchange helpers (provided by other mixins) ──────────────────
    def get_symbol_info(self, symbol: str) -> dict[str, Any]: ...
    def _round_qty(self, qty: float, symbol: str) -> float: ...
    def _save_state(self) -> None: ...

    def _place_algo_order(self, *args: Any, **kwargs: Any) -> Any: ...
    def _cancel_server_order(self, symbol: str, order_id: str, label: str = "order") -> bool: ...
    def _cancel_server_stop_loss(self, symbol: str, order_id: str) -> bool: ...
    def _verify_algo_order_active(self, algo_id: str, symbol: str) -> str | None: ...
    def _place_trailing_stop_market(self, *args: Any, **kwargs: Any) -> Any: ...
    def _cancel_trailing_stop_market(self, symbol: str, order_id: str) -> bool: ...
    def _check_trail_order_status(self, symbol: str, order_id: str) -> dict[str, Any] | None: ...
    def _check_order_filled(self, symbol: str, order_id: str) -> dict[str, Any] | None: ...
    def _lookup_actual_fill(
        self, symbol: str, expected_qty: str, order_type: str
    ) -> dict[str, Any] | None: ...
