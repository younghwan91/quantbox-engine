"""Server-side bracket order management (Algo Order API).

Mixin class providing methods for placing, cancelling, and checking
server-side stop-loss and take-profit orders via Binance Algo API.
Also includes runner SL ratcheting for PBT Phase 2.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.execution.host import ScalperProtocol

import logging
import time
from datetime import datetime, timezone

from binance.exceptions import BinanceAPIException

logger = logging.getLogger("Scalper")


class BracketMixin:
    """Mixin: Server-side bracket orders (SL/TP) via Algo Order API."""

    def _place_algo_order(
        self: "ScalperProtocol",
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        trigger_price: float,
        limit_price: float | None = None,
        label: str = "order",
        position_side: str | None = None,
    ) -> str | None:
        """Place a conditional order via Binance Algo Order API.

        Binance migrated STOP/STOP_MARKET/TAKE_PROFIT/TAKE_PROFIT_MARKET
        to POST /fapi/v1/algoOrder with algoType=CONDITIONAL.

        Args:
            symbol: Trading symbol.
            side: 'BUY' or 'SELL'.
            order_type: 'STOP', 'STOP_MARKET', 'TAKE_PROFIT', etc.
            quantity: Position quantity.
            trigger_price: Price at which the order triggers.
            limit_price: Limit price for STOP/TAKE_PROFIT. None for MARKET types.
            label: Description for logging.
            position_side: Hedge Mode position side ('LONG'/'SHORT').
                When set, positionSide is sent instead of reduceOnly.

        Returns:
            algoId string, or None on failure.
        """
        if self.paper_mode:
            return f"paper_{label}_{int(time.time() * 1000)}"

        params: dict = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": str(quantity),
            "triggerPrice": str(trigger_price),
        }
        # Hedge Mode: positionSide is mutually exclusive with reduceOnly
        if position_side is not None:
            params["positionSide"] = position_side
        else:
            params["reduceOnly"] = "true"

        if limit_price is not None:
            params["price"] = str(limit_price)
            params["timeInForce"] = "GTC"

        lp_str = f"${limit_price:.6f}" if limit_price is not None else "N/A"
        logger.debug(
            f"[{symbol}] Algo order request: {label} "
            f"side={side} type={order_type} qty={quantity:.6f} "
            f"trigger=${trigger_price:.6f} limit={lp_str}"
        )

        try:
            resp = self._algo_api.place_order(params)
            algo_id = str(resp["algoId"])
            logger.info(
                f"[{symbol}] SERVER {label.upper()} placed via Algo API: "
                f"{side} {quantity:.6f} @ trigger=${trigger_price:.6f} "
                f"limit={lp_str} (algoId={algo_id})"
            )
            return algo_id
        except Exception as e:
            err_str = str(e)
            logger.error(f"[{symbol}] SERVER {label.upper()} FAILED: {e}. params={params}")
            # -2021: "Order would immediately trigger" — price already past
            # the stop level. Return sentinel so caller can force-close.
            if "-2021" in err_str:
                return "WOULD_TRIGGER"
            return None

    def _place_server_stop_loss(
        self: "ScalperProtocol",
        symbol: str,
        signal: int,
        quantity: float,
        entry_price: float,
        move_size: float,
        stop_mult: float,
        position_side: str | None = None,
    ) -> str | None:
        """Place a server-side stop-loss order on Binance as a safety net.

        This ensures positions are protected even if the client process crashes.
        The stop price is set at the strategy's stop-loss level (move_size * stop_mult)
        with a 0.5% buffer beyond to account for slippage in fast markets.

        Uses Binance Algo Order API (POST /fapi/v1/algoOrder, algoType=CONDITIONAL).

        Args:
            symbol: Trading symbol.
            signal: +1 (long) or -1 (short).
            quantity: Position quantity.
            entry_price: Fill price.
            move_size: Magnitude of the triggering move.
            stop_mult: Stop loss multiplier.
            position_side: Hedge Mode ('LONG'/'SHORT'). None = One-Way.

        Returns:
            algoId string, or None on failure (position still tracked).
        """
        if self.paper_mode:
            return f"paper_sl_{int(time.time() * 1000)}"

        # C1: Guard — stop_mult must be positive (negative = inverted SL direction)
        if stop_mult < 0:
            logger.error(
                f"[{symbol}] SL INVARIANT: stop_mult={stop_mult:.4f} < 0 — would place SL in wrong direction. Aborting."
            )
            return None

        # Stop price: entry ± (move_size * stop_mult) with 0.5% buffer
        stop_distance = move_size * stop_mult
        buffer = 0.005  # 0.5% extra beyond stop to ensure fill

        if signal == 1:  # Long: stop below entry
            stop_price = entry_price * (1.0 - stop_distance)
            limit_price = stop_price * (1.0 - buffer)  # Sell limit below stop
            close_side = "SELL"
        else:  # Short: stop above entry
            stop_price = entry_price * (1.0 + stop_distance)
            limit_price = stop_price * (1.0 + buffer)  # Buy limit above stop
            close_side = "BUY"

        info = self.get_symbol_info(symbol)
        tick_size = info.get("tick_size", 0.01)
        price_precision = info.get("price_precision", 2)

        # Round prices to tick size
        stop_price = round(round(stop_price / tick_size) * tick_size, price_precision)
        limit_price = round(round(limit_price / tick_size) * tick_size, price_precision)

        # T2-1 runtime guard: sanity check stop price placement
        if entry_price <= 0:
            logger.error(f"[{symbol}] SL INVARIANT: entry_price={entry_price!r} — aborting SL placement")
            return None
        if stop_price <= 0:
            logger.error(f"[{symbol}] SL INVARIANT: stop_price={stop_price!r} — aborting SL placement")
            return None

        dist_pct = stop_distance * 100
        logger.info(
            f"[{symbol}] SL calc: entry=${entry_price:.4f} "
            f"move={move_size * 100:.2f}% × stop_mult={stop_mult:.4f} "
            f"→ dist={dist_pct:.2f}% trigger=${stop_price:.4f} "
            f"limit=${limit_price:.4f} side={close_side}"
        )

        algo_id = self._place_algo_order(
            symbol=symbol,
            side=close_side,
            order_type="STOP",
            quantity=quantity,
            trigger_price=stop_price,
            limit_price=limit_price,
            label="stop-loss",
            position_side=position_side,
        )
        if algo_id == "WOULD_TRIGGER":
            logger.error(
                f"[{symbol}] SL WOULD IMMEDIATELY TRIGGER — "
                f"price already past stop level ${stop_price:.4f}. "
                f"Returning sentinel for force-close."
            )
            self.discord.send(
                content=(
                    f"\U0001f6a8 **SL 즉시체결** [{symbol}] — "
                    f"현재가가 SL ${stop_price:.4f}을 이미 이탈. "
                    f"강제 청산 필요."
                )
            )
            return "WOULD_TRIGGER"
        if algo_id is None:
            logger.error(f"[{symbol}] Position is UNPROTECTED — client-side stop still active.")
            self.discord.send(
                content=(
                    f"\u26a0\ufe0f **서버 SL 실패** [{symbol}] — "
                    f"거래소측 보호주문 없음 (클라이언트 SL만 작동). "
                    f"다음 사이클에 재시도."
                )
            )
        return algo_id

    def _place_server_take_profit(
        self: "ScalperProtocol",
        symbol: str,
        signal: int,
        quantity: float,
        entry_price: float,
        move_size: float,
        target_retrace: float,
        position_side: str | None = None,
    ) -> str | None:
        """Place a server-side take-profit order on Binance.

        This captures intra-bar TP moves that would otherwise be missed
        by the 15m polling cycle. Combined with the server stop-loss,
        this creates an OCO-like bracket around the position.

        Uses Binance Algo Order API (POST /fapi/v1/algoOrder, algoType=CONDITIONAL).

        Args:
            symbol: Trading symbol.
            signal: +1 (long) or -1 (short).
            quantity: Position quantity.
            entry_price: Fill price.
            move_size: Magnitude of the triggering move.
            target_retrace: Fraction of move to target (e.g. 0.382).

        Returns:
            algoId string, or None on failure.
        """
        if self.paper_mode:
            return f"paper_tp_{int(time.time() * 1000)}"

        # D1/D4: Guard — target_retrace must be positive (zero/negative = inverted or no TP)
        # and move_size × target_retrace must not exceed 100% (SHORT trigger_price would go ≤ 0)
        if target_retrace <= 0:
            logger.error(
                f"[{symbol}] TP INVARIANT: target_retrace={target_retrace:.4f} ≤ 0 — would invert or zero TP. Aborting."
            )
            return None
        if move_size * target_retrace >= 1.0:
            logger.error(
                f"[{symbol}] TP INVARIANT: move_size={move_size:.4f} × target_retrace={target_retrace:.4f} "
                f"= {move_size * target_retrace:.4f} ≥ 1.0 — SHORT trigger_price would be ≤ 0. Aborting."
            )
            return None

        # TP price: entry ± (move_size * target_retrace)
        target_distance = move_size * target_retrace
        buffer = 0.002  # 0.2% inside target to ensure fill

        if signal == 1:  # Long: TP above entry
            trigger_price = entry_price * (1.0 + target_distance)
            limit_price = trigger_price * (1.0 - buffer)
            close_side = "SELL"
        else:  # Short: TP below entry
            trigger_price = entry_price * (1.0 - target_distance)
            limit_price = trigger_price * (1.0 + buffer)
            close_side = "BUY"

        info = self.get_symbol_info(symbol)
        tick_size = info.get("tick_size", 0.01)
        price_precision = info.get("price_precision", 2)

        # Round prices to tick size
        trigger_price = round(round(trigger_price / tick_size) * tick_size, price_precision)
        limit_price = round(round(limit_price / tick_size) * tick_size, price_precision)

        # T2-1 runtime guard: sanity check TP price placement
        if entry_price <= 0:
            logger.error(f"[{symbol}] TP INVARIANT: entry_price={entry_price!r} — aborting TP placement")
            return None
        if trigger_price <= 0:
            logger.error(f"[{symbol}] TP INVARIANT: trigger_price={trigger_price!r} — aborting TP placement")
            return None

        dist_pct = target_distance * 100
        logger.info(
            f"[{symbol}] TP calc: entry=${entry_price:.4f} "
            f"move={move_size * 100:.2f}% × retrace={target_retrace:.3f} "
            f"→ dist={dist_pct:.2f}% trigger=${trigger_price:.4f} "
            f"limit=${limit_price:.4f} side={close_side}"
        )

        algo_id = self._place_algo_order(
            symbol=symbol,
            side=close_side,
            order_type="TAKE_PROFIT",
            quantity=quantity,
            trigger_price=trigger_price,
            limit_price=limit_price,
            label="take-profit",
            position_side=position_side,
        )
        if algo_id is None:
            logger.warning(f"[{symbol}] Server TP failed — will rely on client-side TP check.")
        return algo_id

    def _cancel_server_order(self: "ScalperProtocol", symbol: str, order_id: str, label: str = "order") -> bool:
        """Cancel a server-side algo order (stop-loss or take-profit).

        Uses Binance Algo Order API (DELETE /fapi/v1/algoOrder).
        Must be called BEFORE placing a close order to avoid the server
        order triggering while we're trying to close.

        Args:
            symbol: Trading symbol.
            order_id: The algoId to cancel.
            label: Description for logging (e.g. 'stop-loss', 'take-profit').

        Returns:
            True on successful cancel or benign errors (already cancelled/filled/expired).
            False on unexpected API failures.
        """
        if self.paper_mode or not order_id or order_id.startswith("paper_"):
            return True

        try:
            resp = self._algo_api.cancel_order(int(order_id))
            logger.info(f"[{symbol}] Server {label} cancelled: algoId={order_id} resp={resp}")
            return True
        except BinanceAPIException as e:
            err_str = str(e)
            # -2011: Unknown order, -25029: algo order not found, -4000: invalid order id
            # -20012: order already closed — all are benign (order is already gone)
            if (
                "Unknown" in err_str
                or "-2011" in err_str
                or "-25029" in err_str
                or "-4000" in err_str
                or "-20012" in err_str
            ):
                # Already cancelled, filled, or expired — OK
                logger.debug(f"[{symbol}] Server {label} already gone: algoId={order_id} ({e})")
                return True
            logger.warning(f"[{symbol}] Failed to cancel server {label}: algoId={order_id} error={e}")
            return False
        except Exception as e:
            logger.warning(f"[{symbol}] Unexpected error cancelling server {label}: algoId={order_id} error={e}")
            return False

    def _cancel_server_stop_loss(self: "ScalperProtocol", symbol: str, order_id: str) -> bool:
        """Cancel the server-side stop-loss.

        Returns:
            True on success or benign error, False on unexpected failure.
        """
        return self._cancel_server_order(symbol, order_id, "stop-loss")

    def _cancel_server_take_profit(self: "ScalperProtocol", symbol: str, order_id: str) -> bool:
        """Cancel the server-side take-profit.

        Returns:
            True on success or benign error, False on unexpected failure.
        """
        return self._cancel_server_order(symbol, order_id, "take-profit")

    # ── Server-Side Trailing Stop (TRAILING_STOP_MARKET) ──────────────────
    # r90 migration: replaces dead client-side trail + ratchet with Binance's
    # native tick-by-tick trailing stop. Uses Algo Order API with
    # activationPrice = entry_price (already reached → no -2021 rejection).

    def _get_trail_params(
        self: "ScalperProtocol",
        signal: int,
        entry_price: float,
    ) -> tuple[float, float, str]:
        """Calculate TRAILING_STOP_MARKET parameters from config.

        Args:
            signal: +1 (LONG) or -1 (SHORT).
            entry_price: Position entry price.

        Returns:
            (callback_rate, activation_price, close_side) tuple.
            callback_rate: Binance callbackRate (0.1-5.0, percentage).
            activation_price: entry × (1 + trail_activate) for LONG,
                              entry × (1 - trail_activate) for SHORT.
            close_side: 'SELL' for LONG, 'BUY' for SHORT.
        """
        td = self.trail_config.trail_distance
        ta = self.trail_config.trail_activate

        # trail_distance fraction → Binance callbackRate percentage
        # e.g. 0.005 (0.5%) → 0.5, clamped to [0.5, 5.0] (rule #8: callbackRate >= 0.5%)
        callback_rate = max(0.5, min(5.0, td * 100))

        # activationPrice = entry × (1 ± trail_activate).
        # Trail stays in NEW status until price reaches this level,
        # then activates and starts tracking the peak.
        # API accepts future prices (verified via live test 2026-03-19).
        if signal == 1:  # LONG: activate when price goes UP
            activation_price = entry_price * (1 + ta)
        else:  # SHORT: activate when price goes DOWN
            activation_price = entry_price * (1 - ta)

        close_side = "SELL" if signal == 1 else "BUY"

        return callback_rate, activation_price, close_side

    def _place_trailing_stop_market(
        self: "ScalperProtocol",
        symbol: str,
        side: str,
        quantity: float,
        callback_rate: float,
        activation_price: float,
        position_side: str | None = None,
    ) -> str | None:
        """Place TRAILING_STOP_MARKET via Algo Order API (POST /fapi/v1/algoOrder).

        The regular futures API rejects TRAILING_STOP_MARKET with -4120.
        Algo API is the only supported endpoint. With activationPrice = entry_price
        (already reached), -2021 rejection is avoided.
        """
        if self.paper_mode:
            return f"paper_trail_{int(time.time() * 1000)}"

        params: dict = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": side,
            "type": "TRAILING_STOP_MARKET",
            "quantity": str(quantity),
            "callbackRate": str(callback_rate),
            "activationPrice": str(activation_price),
        }
        # Hedge Mode: positionSide is mutually exclusive with reduceOnly
        if position_side is not None:
            params["positionSide"] = position_side
        else:
            params["reduceOnly"] = "true"

        logger.debug(
            f"[{symbol}] Trail order request: side={side} qty={quantity:.6f} "
            f"callbackRate={callback_rate}% activationPrice=${activation_price:.4f}"
        )

        try:
            resp = self._algo_api.place_order(params)
            algo_id = str(resp["algoId"])
            logger.info(
                f"[{symbol}] SERVER TRAIL placed via Algo API: "
                f"{side} {quantity:.6f} callbackRate={callback_rate}% "
                f"activationPrice=${activation_price:.4f} (algoId={algo_id})"
            )
            return algo_id
        except Exception as e:
            err_str = str(e)
            # -2021: Order would immediately trigger — mark price is too far
            # from activationPrice for Binance to accept the order.
            # Return sentinel so caller can suppress retry spam.
            if "-2021" in err_str:
                logger.info(
                    f"[{symbol}] Trail WOULD_TRIGGER — mark too far from "
                    f"activationPrice=${activation_price:.6f} (callbackRate={callback_rate}%)"
                )
                return "WOULD_TRIGGER"
            logger.error(f"[{symbol}] SERVER TRAIL FAILED: {e}. params={params}")
            return None

    def _cancel_trailing_stop_market(
        self: "ScalperProtocol",
        symbol: str,
        order_id: str,
    ) -> bool:
        """Cancel TRAILING_STOP_MARKET via Algo Order API (DELETE /fapi/v1/algoOrder)."""
        return self._cancel_server_order(symbol, order_id, "trailing-stop")

    def _check_trail_order_status(
        self: "ScalperProtocol",
        symbol: str,
        order_id: str,
    ) -> dict | None:
        """Check TRAILING_STOP_MARKET via Algo Order API (GET /fapi/v1/algoOrder)."""
        return self._check_order_filled(symbol, order_id)

    # ── Trail API Facade ────────────────────────────────────────────────
    # These methods are called by positions.py / reconciliation.py.
    # Internally they use the Algo Order API — the regular futures API
    # rejects TRAILING_STOP_MARKET with -4120.

    def _place_trail_regular(
        self: "ScalperProtocol",
        symbol: str,
        side: str,
        quantity: float,
        callback_rate: float,
        activation_price: float | None = None,
        position_side: str | None = None,
    ) -> str | None:
        """Place TRAILING_STOP_MARKET order.

        Delegates to Algo Order API (``_place_trailing_stop_market``).
        Despite the method name, the regular API (POST /fapi/v1/order) returns
        -4120 for this order type — only the Algo API works.

        Returns:
            algoId as string on success, None on failure.
        """
        if activation_price is None:
            activation_price = 0.0  # shouldn't happen — caller always provides
        return self._place_trailing_stop_market(
            symbol=symbol,
            side=side,
            quantity=quantity,
            callback_rate=callback_rate,
            activation_price=activation_price,
            position_side=position_side,
        )

    def _cancel_trail_regular(
        self: "ScalperProtocol",
        symbol: str,
        order_id: str,
    ) -> bool:
        """Cancel a TRAILING_STOP_MARKET order.

        Delegates to Algo Order API (``_cancel_trailing_stop_market``).
        """
        return self._cancel_trailing_stop_market(symbol, order_id)

    def _check_trail_regular(
        self: "ScalperProtocol",
        symbol: str,
        order_id: str,
    ) -> dict | None:
        """Check TRAILING_STOP_MARKET order status.

        Delegates to Algo Order API (``_check_trail_order_status``).
        Algo order status lifecycle: NEW → WORKING → TRIGGERED → FINISHED.
        Both TRIGGERED and FINISHED are treated as FILLED.
        """
        return self._check_trail_order_status(symbol, order_id)

    def _lookup_actual_fill(
        self: "ScalperProtocol",
        symbol: str,
        expected_qty: str,
        order_type: str,
    ) -> dict | None:
        """Look up actual fill price/qty from recent account trades.

        v12.5.1: Called after algo order TRIGGERED to get real execution
        data instead of relying on triggerPrice as proxy.

        Args:
            symbol: Trading pair.
            expected_qty: Expected fill quantity (from algo order).
            order_type: 'STOP' or 'TAKE_PROFIT' to determine close side.

        Returns:
            dict with 'price' and 'qty' if found, None otherwise.
        """
        if self.paper_mode:
            return None
        try:
            recent_trades = self.client.futures_account_trades(
                symbol=symbol,
                limit=20,
            )
            if not recent_trades:
                return None

            # Find the most recent trade with realized PnL (reduce-only fill)
            expected_q = float(expected_qty) if expected_qty else 0
            for t in reversed(recent_trades):
                rpnl = float(t.get("realizedPnl", "0"))
                tqty = float(t.get("qty", "0"))
                if rpnl != 0 and abs(tqty - expected_q) / max(expected_q, 1e-8) < 0.05:
                    return {
                        "price": float(t["price"]),
                        "qty": tqty,
                    }
            # Broader fallback: any recent trade with realized PnL
            for t in reversed(recent_trades):
                rpnl = float(t.get("realizedPnl", "0"))
                if rpnl != 0:
                    return {
                        "price": float(t["price"]),
                        "qty": float(t.get("qty", "0")),
                    }
        except Exception as e:
            logger.warning(f"[{symbol}] Failed to lookup actual fill: {e}")
        return None

    def _check_order_filled(self: "ScalperProtocol", symbol: str, order_id: str) -> dict | None:
        """Check if a server-side algo order has been filled.

        Uses Binance Algo Order API (GET /fapi/v1/algoOrder).
        Conditional algo orders transition through these states:
          WORKING → TRIGGERED (brief) → FINISHED (terminal, filled)
        We check for both TRIGGERED and FINISHED status.

        v14.0.1: Fixed — Binance returns algoStatus='FINISHED' (not
        'TRIGGERED') for conditional orders that have triggered AND
        the resulting sub-order has filled. The old code only checked
        for 'TRIGGERED', causing ALL server-side fills to be missed
        and detected only by the fallback _reconcile_positions().

        Returns a dict with 'avgPrice' and 'executedQty' if filled,
        None otherwise.
        """
        if self.paper_mode or not order_id or order_id.startswith("paper_"):
            return None

        try:
            resp = self._algo_api.get_order(int(order_id))
            algo_status = resp.get("algoStatus", "")
            logger.info(
                f"[{symbol}] Algo order check: algoId={order_id} status={algo_status} type={resp.get('orderType', '?')}"
            )

            # v14.0.1: FINISHED is the terminal state for a conditional
            # order that has triggered and whose sub-order has filled.
            # TRIGGERED is a brief intermediate state (trigger fired,
            # sub-order placed but not yet filled). Both mean "filled".
            if algo_status in ("TRIGGERED", "FINISHED"):
                # Extract fill info from the algo order response.
                # FINISHED provides actualPrice/actualQty from the
                # actual sub-order fill; TRIGGERED may only have
                # triggerPrice as a proxy.
                actual_price = resp.get("actualPrice", "0")
                actual_qty = resp.get("actualQty") or resp.get("quantity", "0")
                trigger_price = resp.get("triggerPrice", "0")

                logger.info(
                    f"[{symbol}] Algo order {algo_status}: algoId={order_id} "
                    f"type={resp.get('orderType')} "
                    f"triggerPrice={trigger_price} "
                    f"actualPrice={actual_price} qty={actual_qty}"
                )

                # v12.5.1: Get ACTUAL fill price/qty from account trades
                # instead of using triggerPrice as proxy (which can differ
                # by 0.1-0.5% from real execution during fast moves).
                real_fill = self._lookup_actual_fill(
                    symbol,
                    actual_qty,
                    resp.get("orderType", ""),
                )
                if real_fill:
                    fill_price = real_fill["price"]
                    fill_qty = real_fill["qty"]
                    logger.info(f"[{symbol}] Actual fill from trades: price=${fill_price:.4f} qty={fill_qty}")
                else:
                    # Fallback: use actualPrice or triggerPrice
                    fill_price = float(actual_price)
                    if fill_price == 0:
                        fill_price = float(trigger_price)
                    fill_qty = actual_qty
                    logger.warning(f"[{symbol}] Using algo trigger/actual price as fill proxy: ${fill_price:.4f}")

                return {
                    "avgPrice": str(fill_price),
                    "executedQty": str(fill_qty),
                    "status": "FILLED",
                    "algoId": order_id,
                }

            # v12.3.1: Detect externally cancelled/expired orders.
            # Without this, a cancelled order keeps its algoId in our state,
            # _sync_server_orders skips bracket repair (oid is not None),
            # and the position silently loses server-side protection.
            if algo_status in ("CANCELLED", "CANCELED", "EXPIRED", "USER_CANCELLED", "ERROR"):
                logger.warning(f"[{symbol}] Algo order {algo_status}: algoId={order_id} — will trigger bracket repair")
                return {"status": "CANCELLED", "algoId": order_id}
        except Exception as e:
            logger.warning(f"[{symbol}] Algo order status check failed: algoId={order_id} error={e}")
        return None

    def _ratchet_runner_sl(self: "ScalperProtocol", symbol: str, pos: dict) -> None:
        """Ratchet server-side SL for a Phase 2 runner to protect profits.

        v12.3.3: The runner's server SL starts at breakeven (stop_mult=0.0001).
        As the runner's trailing stop floor climbs above breakeven, the server
        SL should follow — otherwise a bot crash would lose all runner profits
        above breakeven.

        Only ratchets UPWARD (never moves SL against the position direction).
        Checks every cycle but only places a new order when the computed
        trail_stop has improved meaningfully (≥0.5% above last server SL).

        Args:
            symbol: Trading symbol.
            pos: Position dict (mutated in place to update server_sl_order_id).
        """
        trail_stop_pct = pos.get("_computed_trail_stop")
        if trail_stop_pct is None or trail_stop_pct <= 0:
            return  # No improvement over breakeven yet

        # Only ratchet if meaningful improvement (≥0.5% absolute gain above last)
        last_server_floor = pos.get("_server_sl_pct", 0.0)
        improvement = trail_stop_pct - last_server_floor
        if improvement < 0.005:  # 0.5% minimum step to avoid API spam
            return

        # Extract Binance symbol from compound key (e.g. BTCUSDT:LONG → BTCUSDT).
        # Strip defensively in case pos['symbol'] was corrupted by old _load_state.
        raw_sym = pos.get("symbol", symbol)
        api_sym = raw_sym.split(":")[0] if ":" in raw_sym else raw_sym

        entry_price = pos["entry_price"]
        signal = pos["signal"]

        # Convert trail_stop (return pct) to trigger price
        if signal == 1:  # Long: SL below
            trigger_price = entry_price * (1.0 + trail_stop_pct)
            close_side = "SELL"
            buffer = -0.005  # 0.5% below trigger for limit
        else:  # Short: SL above
            trigger_price = entry_price * (1.0 - trail_stop_pct)
            close_side = "BUY"
            buffer = 0.005  # 0.5% above trigger for limit

        limit_price = trigger_price * (1.0 + buffer)

        info = self.get_symbol_info(api_sym)
        tick_size = info.get("tick_size", 0.01)
        price_precision = info.get("price_precision", 2)

        trigger_price = round(round(trigger_price / tick_size) * tick_size, price_precision)
        limit_price = round(round(limit_price / tick_size) * tick_size, price_precision)

        # v12.3.3 SAFETY: Place new SL FIRST, then cancel old.
        # Reversing this order (cancel-then-place) leaves the runner
        # unprotected if the bot crashes between the two API calls.
        # Binance allows multiple algo stop orders to coexist briefly.
        quantity = pos["notional"] / pos["entry_price"]
        quantity = self._round_qty(quantity, api_sym)
        if quantity <= 0:
            return

        new_sl = self._place_algo_order(
            symbol=api_sym,
            side=close_side,
            order_type="STOP",
            quantity=quantity,
            trigger_price=trigger_price,
            limit_price=limit_price,
            label="runner-trail-sl",
            position_side=pos.get("position_side"),
        )

        if new_sl and new_sl != "WOULD_TRIGGER":
            # Success — now cancel old SL (runner always protected)
            old_sl = pos.get("server_sl_order_id")
            if old_sl:
                self._cancel_server_stop_loss(api_sym, old_sl)
            pos["server_sl_order_id"] = new_sl
            pos["_server_sl_pct"] = trail_stop_pct
            logger.info(
                f"[{symbol}] Runner SL RATCHETED: "
                f"{last_server_floor * 100:.2f}% → {trail_stop_pct * 100:.2f}% "
                f"(trigger=${trigger_price:.4f})"
            )
            self._save_state()
        elif new_sl == "WOULD_TRIGGER":
            # Price already past trail stop — keep old SL, runner exits next cycle
            logger.warning(
                f"[{symbol}] Runner SL ratchet WOULD_TRIGGER — "
                f"keeping old SL, update_position should catch this next bar"
            )
        else:
            # Placement failed — keep old SL active (don't cancel!)
            logger.warning(f"[{symbol}] Runner SL ratchet FAILED — keeping old SL at {last_server_floor * 100:.2f}%")

    def _ratchet_trail_sl(self: "ScalperProtocol", symbol: str, pos: dict) -> None:
        """Ratchet server SL to trail_stop_gain price after trail activation.

        r65: bar-close execution gap fix. Backtest assumes intrabar trail stop at
        exact trail_stop_gain price; live code exits at bar-close (MARKET order).
        This places/updates a Binance STOP order at the exact trail_stop_gain price
        so Binance executes intrabar — matching backtest behavior.

        Only ratchets UPWARD (never moves SL against position direction).
        Guards against API spam with a minimum 0.1% improvement threshold.

        Args:
            symbol: Compound position key (e.g. "BTCUSDT:LONG").
            pos: Position dict (mutated to update server_sl_order_id).
        """
        peak_gain = pos.get("peak_gain", 0.0)
        if not pos.get("trail_active") or self.paper_mode:
            return

        trail_distance = self.trail_config.trail_distance
        trail_stop_gain = max(peak_gain - trail_distance, 0.0)

        # Only ratchet when meaningfully better than last ratcheted level
        last_ratcheted = pos.get("_trail_sl_ratcheted", -999.0)
        if trail_stop_gain - last_ratcheted < 0.001:  # 0.1% minimum step
            return

        raw_sym = pos.get("symbol", symbol)
        api_sym = raw_sym.split(":")[0] if ":" in raw_sym else raw_sym

        entry_price = pos["entry_price"]
        signal = pos["signal"]

        if signal == 1:  # LONG: SL is below current price
            trigger_price = entry_price * (1.0 + trail_stop_gain)
            close_side = "SELL"
            buffer = -0.003  # 0.3% below trigger for limit
        else:  # SHORT: SL is above current price
            trigger_price = entry_price * (1.0 - trail_stop_gain)
            close_side = "BUY"
            buffer = 0.003  # 0.3% above trigger for limit

        limit_price = trigger_price * (1.0 + buffer)

        info = self.get_symbol_info(api_sym)
        tick_size = info.get("tick_size", 0.01)
        price_precision = info.get("price_precision", 2)

        trigger_price = round(round(trigger_price / tick_size) * tick_size, price_precision)
        limit_price = round(round(limit_price / tick_size) * tick_size, price_precision)

        # Place-first-cancel-after: position never left unprotected
        quantity = pos["notional"] / pos["entry_price"]
        quantity = self._round_qty(quantity, api_sym)
        if quantity <= 0:
            return

        new_sl = self._place_algo_order(
            symbol=api_sym,
            side=close_side,
            order_type="STOP",
            quantity=quantity,
            trigger_price=trigger_price,
            limit_price=limit_price,
            label="trail-ratchet-sl",
            position_side=pos.get("position_side"),
        )

        if new_sl and new_sl != "WOULD_TRIGGER":
            old_sl = pos.get("server_sl_order_id")
            if old_sl:
                self._cancel_server_stop_loss(api_sym, old_sl)
            pos["server_sl_order_id"] = new_sl
            pos["_trail_sl_ratcheted"] = trail_stop_gain
            logger.info(
                f"[{symbol}] Trail SL RATCHETED: "
                f"trail_stop={trail_stop_gain * 100:.3f}% peak={peak_gain * 100:.3f}% "
                f"trigger=${trigger_price:.4f}"
            )
            self._save_state()
        elif new_sl == "WOULD_TRIGGER":
            logger.warning(
                f"[{symbol}] Trail SL ratchet WOULD_TRIGGER — "
                f"bar-close exit will handle (trail_stop={trail_stop_gain * 100:.3f}%)"
            )
        else:
            logger.warning(f"[{symbol}] Trail SL ratchet FAILED — old SL preserved")

    def _verify_algo_order_active(self: "ScalperProtocol", algo_id: str, symbol: str) -> str | None:
        """Verify an algo order is still active on Binance.

        Args:
            algo_id: The algoId to query.
            symbol: Trading symbol (for logging).

        Returns:
            "WORKING" if active, "EXPIRED"/"CANCELLED" if dead, None on query failure.
        """
        if self.paper_mode:
            return "WORKING"
        try:
            resp = self._algo_api.get_order(int(algo_id))
            # Response may vary — handle both dict and list
            if isinstance(resp, dict):
                return resp.get("algoStatus") or resp.get("status")
            return None
        except Exception as e:
            logger.warning(f"[{symbol}] Algo order status query failed for {algo_id}: {e}")
            return None

    def _check_bracket_staleness(self: "ScalperProtocol") -> None:
        """Check if any SL algo orders have expired or been cancelled.

        Called each cycle from _sync_server_orders(). Detects externally cancelled
        or expired SL orders so they can be re-placed by normal bracket repair logic.
        """
        if self.paper_mode:
            return
        for sym in list(self.strategy.active_positions):
            pos = self.strategy._positions.get(sym)
            if not pos:
                continue
            sl_oid = pos.get("server_sl_order_id")
            if not sl_oid or str(sl_oid).startswith("paper_"):
                continue
            api_sym = pos.get("symbol", sym.split(":")[0])
            if ":" in api_sym:
                api_sym = api_sym.split(":")[0]
            status = self._verify_algo_order_active(str(sl_oid), api_sym)
            if status in ("EXPIRED", "CANCELLED", "CANCELED", "USER_CANCELLED", "ERROR"):
                logger.warning(f"[{sym}] SL algo order {sl_oid} is {status} — re-placing bracket")
                pos["server_sl_order_id"] = None
                # Bracket will be re-placed by the normal bracket repair logic
                self.discord.send(content=f"⚠️ **[{sym}] SL 브라켓 {status}** — 재배치 예정")
                pos["bracket_sl_verified_at"] = datetime.now(timezone.utc).isoformat()
            elif status == "WORKING":
                pos["bracket_sl_verified_at"] = datetime.now(timezone.utc).isoformat()

    def _verify_ratchet(self: "ScalperProtocol", sym: str, pos: dict, expected_price: float) -> bool:
        """Verify a ratchet SL update was accepted by Binance.

        On failure: keeps old SL active + alerts. Does NOT force-close.
        Force-close ONLY if no SL exists at all.

        Args:
            sym: Compound position key.
            pos: Position dict.
            expected_price: The trigger price of the newly placed ratchet SL.

        Returns:
            True if verified OK, False if verification failed or no SL at all.
        """
        sl_oid = pos.get("server_sl_order_id")
        if not sl_oid:
            # No SL at all — this IS the unprotected case
            logger.critical(f"[{sym}] NO SL EXISTS after ratchet — force-closing position")
            self.discord.send(content=f"🚨 **[{sym}] SL 없음** — 강제 청산")
            return False

        api_sym = pos.get("symbol", sym.split(":")[0])
        if ":" in api_sym:
            api_sym = api_sym.split(":")[0]
        status = self._verify_algo_order_active(str(sl_oid), api_sym)
        if status != "WORKING":
            # Ratchet failed — keep old SL, alert
            logger.warning(f"[{sym}] Ratchet verification failed: SL {sl_oid} status={status}. Keeping old SL.")
            self.discord.send(content=f"⚠️ **[{sym}] 래칫 검증 실패** — 기존 SL 유지, status={status}")
            return False

        # Log ratchet history
        pos.setdefault("_ratchet_history", [])
        pos["_ratchet_history"].append(
            {
                "price": expected_price,
                "time": datetime.now(timezone.utc).isoformat(),
                "verified": True,
            }
        )
        pos["last_ratchet_price"] = expected_price
        pos["last_ratchet_at"] = datetime.now(timezone.utc).isoformat()
        return True
