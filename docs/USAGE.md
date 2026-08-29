# 사용 가이드

설계 배경은 [ARCHITECTURE.md](ARCHITECTURE.md), 요약은 [README](../README.md) 참고.

## 1. 실데이터로 백테스트

`high/low/close` 배열만 있으면 된다. CSV(컬럼: `high,low,close`)는 `load_csv` 로 바로 읽는다.

```python
from engine.backtest.vectorized import run_backtest
from engine.data.klines import load_csv
from engine.strategy.demo_squeeze import SqueezeStrategy

high, low, close = load_csv("BTCUSDT_1h.csv")
result = run_backtest(
    SqueezeStrategy(),
    high, low, close,
    fee=0.0004,       # 테이커 수수료 (레그당)
    slippage=0.0002,  # 슬리피지 (레그당)
    warmup=100,       # 지표 워밍업에 쓸 앞쪽 봉 수
)
print(result.summary())
# {'n_trades': ..., 'total_return': ..., 'win_rate': ..., 'profit_factor': ..., 'max_drawdown': ...}
```

`result.trades` 에 개별 트레이드(진입·청산 인덱스, 청산 사유, net 수익률)가 들어 있다. 수수료와 슬리피지는 진입·청산 양쪽 레그에 매겨지므로 지표는 모두 net 이다. 펀딩비는 모델에 없다.

키도 네트워크도 없이 돌려보려면 `engine.data.klines.synth_ohlcv` 로 합성 데이터를 만들면 된다. `qbe-backtest --demo` 가 쓰는 게 이것이다.

## 2. 내 전략 붙이기

[`TradingStrategy`](../engine/strategy/protocol.py) 는 PEP 544 프로토콜이라 상속이 필요 없다. 아래 메서드만 있으면 백테스터와 라이브 양쪽에서 그대로 돈다. 시그널은 `-1`(숏) / `0`(관망) / `+1`(롱).

```python
class MyStrategy:
    def __init__(self):
        self._positions: dict[str, dict] = {}

    # ── 포지션 장부 ──────────────────────────────
    @property
    def active_positions(self) -> dict[str, dict]:
        return {k: dict(v) for k, v in self._positions.items()}

    def has_position(self, symbol: str) -> bool:
        return symbol in self._positions

    # ── 데이터 피드 ──────────────────────────────
    def update_market_data(self, symbol, high, low, close) -> None:
        ...  # 지표용 배열 저장 (받는 배열엔 미래 봉이 없다)

    # ── 시그널 ──────────────────────────────────
    def on_bar(self, symbol, close) -> int:
        return 0

    def get_last_atr_pct(self, symbol) -> float:
        return 0.0  # 사이징용 ATR (가격 대비 비율)

    # ── 포지션 라이프사이클 ───────────────────────
    def open_position(self, symbol, signal, entry_price, atr_pct) -> None:
        self._positions[symbol] = {"signal": signal, "entry_price": entry_price}

    def update_position(self, symbol, price, high=None, low=None) -> str | None:
        return None  # 청산 사유 문자열(예: "stop_loss") 또는 None

    def close_position(self, symbol) -> dict | None:
        return self._positions.pop(symbol, None)
```

가장 짧은 레퍼런스 구현은 [`demo_squeeze.py`](../engine/strategy/demo_squeeze.py) 다.

### 호출 순서 (봉 하나)

```
매 봉 t 마다:
  update_market_data(symbol, high[:t+1], low[:t+1], close[:t+1])   # 마감된 봉만
  ├─ 포지션 없음 → on_bar() 로 시그널 확인, 있으면 open_position()
  └─ 포지션 있음 → update_position() 으로 청산 조건 체크, 걸리면 close_position()
```

라이브 봇도 정확히 이 순서로 같은 메서드를 부른다. 백테스트와 실거래가 갈라질 자리가 없다.

## 3. 라이브 실행 (선택)

`engine.execution` 은 바이낸스 USDT-M 선물에 실제 주문을 태우는 레이어라 `python-binance` 가 필요하다.

```bash
uv pip install -e ".[live]"
cp .env.example .env   # 바이낸스 API 키 (.env 는 커밋 금지)
```

- [`brackets.py`](../engine/execution/brackets.py) — 진입 직후 SL/TP/트레일링을 거래소 **Algo Order** 로 걸어둔다. 가격이 유리하게 가면 트레일링 스탑을 서버사이드에서 래칫(한 방향으로만 조임)한다. 봇 프로세스가 죽어도 청산은 거래소가 책임진다.
- [`algo_api.py`](../engine/execution/algo_api.py) — 바이낸스 Algo Order REST 래퍼.
- [`host.py`](../engine/execution/host.py) — 실행 믹스인이 기대하는 호스트 인터페이스와 `TrailConfig`.

트레일링 폭은 알파가 아니라 범용 `TrailConfig` 로 빼뒀다. 사설 시스템의 전략별 튜닝값은 이 저장소에 없다.

## 개발

```bash
uv run pytest -q        # 네트워크·API 키 불필요
uv run ruff check .
```
