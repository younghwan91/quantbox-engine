"""Algo Order API client — wraps Binance private _request_futures_api calls.

Phase 3.3 (refactor/mega-v2): extracts all Algo API calls into a single
class so the rest of the codebase doesn't depend on python-binance internals.
If python-binance adds official Algo API support, only this file needs updating.

Binance Algo Order API endpoints (undocumented in python-binance):
  POST   /fapi/v1/algoOrder      — place conditional/trailing order
  DELETE /fapi/v1/algoOrder      — cancel by algoId
  GET    /fapi/v1/algoOrder      — query single order status
  GET    /fapi/v1/openAlgoOrders — list all open algo orders
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("Scalper")


class AlgoApiClient:
    """Thin wrapper around Binance Algo Order API.

    Args:
        client: binance.client.Client instance with _request_futures_api method.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    def place_order(self, params: dict[str, Any]) -> dict[str, Any]:
        """Place an algo order (STOP, TAKE_PROFIT, TRAILING_STOP_MARKET).

        Args:
            params: Full request params dict including algoType, symbol, side, etc.

        Returns:
            Response dict with algoId on success.

        Raises:
            Exception: On API error (caller handles error codes like -2021).
        """
        return self._client._request_futures_api("post", "algoOrder", signed=True, data=params)

    def cancel_order(self, algo_id: int) -> dict[str, Any]:
        """Cancel an algo order by ID.

        Args:
            algo_id: The algoId returned from place_order.

        Returns:
            Response dict on success.

        Raises:
            Exception: On API error (caller handles benign errors like -2011).
        """
        return self._client._request_futures_api("delete", "algoOrder", signed=True, data={"algoId": algo_id})

    def get_order(self, algo_id: int) -> dict[str, Any]:
        """Query status of a single algo order.

        Args:
            algo_id: The algoId to query.

        Returns:
            Response dict with algoStatus, orderType, etc.

        Raises:
            Exception: On API error.
        """
        return self._client._request_futures_api("get", "algoOrder", signed=True, data={"algoId": algo_id})

    def list_open_orders(self) -> dict[str, Any] | list[Any]:
        """List all open algo orders on the account.

        Returns:
            Response dict with 'orders' list, or a list directly
            (Binance response format varies).

        Raises:
            Exception: On API error.
        """
        return self._client._request_futures_api("get", "openAlgoOrders", signed=True, data={})
