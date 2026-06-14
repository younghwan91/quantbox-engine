# Architecture

QuantBox Engine separates three concerns that are usually tangled in trading
code: **deciding** (strategy), **measuring** (backtest), and **executing**
(live). The engine owns measuring and executing; a strategy only decides. They
meet at one narrow interface, [`TradingStrategy`](../engine/strategy/protocol.py).

```
            ┌───────────────────────────────┐
   OHLCV →  │  Backtester  /  Live Scalper   │  ← same driver, same calls
            └───────────────┬───────────────┘
                            │  TradingStrategy protocol
                            ▼
            ┌───────────────────────────────┐
            │   Strategy (e.g. Squeeze)      │  decides: -1 / 0 / +1, exits
            └───────────────────────────────┘
```

## 1. No-look-ahead by construction

The cheapest way to fake a great backtest is to let a signal see the future.
We remove the possibility instead of policing it: the backtester only ever
hands a strategy `close[: t + 1]`, the prefix of *completed* bars up to the
current one. A strategy literally cannot index into the future because the
future isn't in the array it receives.

This invariant is asserted in
[`test_no_look_ahead_strategy_only_sees_past`](../tests/test_backtest.py): a spy
strategy records every window it is shown and verifies each is an exact past
prefix that grows by one bar per step.

## 2. Backtest / live parity

A strategy is a *stateful object* implementing `TradingStrategy`, not a vector
of precomputed signals. The exact same object is stepped by the backtester and
by the live scalper, calling the same methods in the same order:

```
update_market_data(...)   # feed the latest completed bars
on_bar(...)               # -> signal
open_position(...)        # on entry
update_position(...)      # -> exit reason or None, each subsequent bar
close_position(...)
```

Because there is one implementation of the decision logic, a backtested edge and
a live edge cannot silently diverge from a reimplementation gap. The only
differences live in the *execution* layer (fills, latency, fees), which the
backtester models explicitly.

## 3. Execution layer (live)

The live layer (in [`engine/execution/`](../engine/execution/)) is included as a
reference for how the decision logic reaches the exchange safely. Highlights:

- **Server-side brackets** ([`brackets.py`](../engine/execution/brackets.py)):
  stop-loss / take-profit / trailing stops are placed on Binance's Algo Order
  engine, so risk protection survives a bot crash, restart, or network outage —
  the exchange enforces the exit even if the process is dead.
- **Ratcheting trailing stops**: as price advances, the trailing stop is moved
  (ratcheted) server-side, never loosened, with a minimum step to avoid
  rate-limit churn.
- **Host interface** ([`host.py`](../engine/execution/host.py)): execution is
  composed from mixins typed against a `ScalperProtocol`, so each mixin
  type-checks in isolation without importing a concrete, strategy-specific host.

> Trailing geometry here is driven by a generic `TrailConfig`; the proprietary
> per-strategy tuning from the private system is intentionally not included.

## 4. Cost model

`Trade.net_return` charges `fee + slippage` on **both** the entry and exit legs,
so `BacktestResult.profit_factor`, `win_rate`, and `total_return` are all net of
trading costs. Gross return is available separately for attribution.

## What is intentionally omitted

This is the engine, not the alpha. The proprietary strategies, their parameters,
research notebooks, and live performance figures from the private system are not
part of this repository. The bundled `SqueezeStrategy` is public textbook logic
whose only job is to exercise the engine end to end.
