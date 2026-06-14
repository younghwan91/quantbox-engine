"""Kline (candlestick) loading and a synthetic generator for offline demos.

:func:`load_csv` reads a standard OHLCV CSV; :func:`synth_ohlcv` fabricates a
deterministic series with alternating low- and high-volatility regimes so the
bundled demo runs end-to-end with no network, keys, or vendor data.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def load_csv(path: str | Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load ``high, low, close`` arrays from an OHLCV CSV (case-insensitive)."""
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    return (
        df[cols["high"]].to_numpy(float),
        df[cols["low"]].to_numpy(float),
        df[cols["close"]].to_numpy(float),
    )


def synth_ohlcv(
    n: int = 2000, seed: int = 7, start: float = 100.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Generate a deterministic OHLC series with volatility regime switches.

    Volatility cycles between a quiet "squeeze" phase and an expansion phase, so
    a squeeze strategy has something to react to.  Returns ``(high, low, close)``.
    """
    rng = np.random.default_rng(seed)
    vol = np.where((np.arange(n) // 120) % 2 == 0, 0.004, 0.018)  # quiet ↔ wild
    rets = rng.normal(0, 1, n) * vol
    close = start * np.exp(np.cumsum(rets))
    # Intrabar range scaled by the regime's volatility.
    span = close * vol * rng.uniform(0.5, 1.5, n)
    high = close + span * rng.uniform(0.2, 1.0, n)
    low = close - span * rng.uniform(0.2, 1.0, n)
    return high, low, close
