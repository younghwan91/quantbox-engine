# QuantBox Engine ⚙️

> 이 저장소는 제가 직접 설계·운용하는 바이낸스 USDT-M 선물 자동매매 시스템에서, **전략(알파)과 엔진을 분리해 재사용 가능한 인프라 레이어만 오픈소스로 공개**한 것입니다.

**전략에 종속되지 않는 암호화폐 선물 백테스트·실행 엔진.** 백테스트와 실거래가 같은 코드를 타고, 룩어헤드(미래 데이터 참조)가 구조적으로 끼어들 수 없게 짰다.

수익이 나는 전략 로직은 비공개로 두고, 백테스트·실행·리스크 인프라만 공개한다. 레포에 들어있는 데모 전략은 누구나 아는 교과서 로직(볼린저 밴드 스퀴즈)이다.

> 이 레포가 보여주려는 건 "돈 버는 전략"이 아니라 **"전략을 제대로 검증하고 실거래까지 안전하게 태우는 엔진을 설계하는 능력"**이다. 전략과 엔진의 경계를 깔끔하게 갈라낼 수 있다는 것 자체가 핵심이다.

---

## 핵심 설계

| 설계 | 어떻게 |
|---|---|
| **룩어헤드 원천 차단** | 백테스터가 매 봉마다 `close[:t+1]`(현재까지 마감된 봉)만 전략에 넘긴다. 전략이 받는 배열에 미래 데이터가 애초에 없어서 들여다볼 수가 없다. ([테스트로 검증](tests/test_backtest.py)) |
| **백테스트 = 실거래 동일 코드** | 같은 `TradingStrategy` 객체를 백테스터와 라이브 봇이 똑같이 돌린다. "백테스트는 잘 됐는데 실전은 딴판"인 괴리가 생길 구조가 아니다. |
| **전략 플러그인 구조** | 엔진은 [`TradingStrategy` 인터페이스](engine/strategy/protocol.py)(PEP 544 구조적 서브타이핑)로만 전략과 대화한다. 새 전략은 이 인터페이스만 구현하면 끝. |
| **수수료·슬리피지 반영** | 테이커 수수료 + 슬리피지를 진입/청산 양쪽에 매긴다. 그래서 나오는 수익률이 gross가 아니라 net이다. |
| **서버사이드 청산** | SL/TP·트레일링 스탑을 바이낸스 Algo Order로 거래소에 걸어둔다. 봇이 죽어도 거래소가 청산을 책임진다. ([brackets.py](engine/execution/brackets.py)) |

## 빠른 시작

```bash
uv venv && uv pip install -e ".[dev]"

uv run qbe-backtest --demo      # 합성 데이터로 데모 백테스트
uv run pytest -q                # 테스트 (네트워크·API 키 필요 없음)
```

데모 출력 (합성 데이터 + 볼린저 스퀴즈 전략):

```
QuantBox Engine — demo backtest (Bollinger squeeze, synthetic data)
  trades        : 19
  total return  : +6.00%
  win rate      : 68.4%
  profit factor : 1.33
  max drawdown  : -6.27%
```

> ⚠️ 이 숫자는 **합성 데이터에 데모 전략을 돌린 결과**다. 엔진이 돌아간다는 걸 보여줄 뿐, 수익성과는 아무 상관 없다.

## 프로젝트 구조

```
engine/
├── strategy/              # 전략 레이어 — 무엇을 살지/팔지 결정
│   ├── protocol.py        #   TradingStrategy: 엔진↔전략 사이의 인터페이스
│   └── demo_squeeze.py    #   레퍼런스 구현 (볼린저 스퀴즈 평균회귀)
├── backtest/              # 측정 레이어 — 전략이 과거에 어땠는지
│   └── vectorized.py      #   봉 단위 백테스터 + 성과 지표 (룩어헤드 차단 지점)
├── data/                  # 데이터 레이어
│   ├── klines.py          #   OHLCV CSV 로더 + 오프라인 합성 데이터 생성기
│   └── cache.py           #   메모리 + 디스크(gzip) 2단계 캐시
└── execution/             # 실행 레이어 — 결정을 거래소에 태운다 (.[live] 필요)
    ├── brackets.py        #   서버사이드 SL/TP·트레일링 스탑 (래칫 포함)
    ├── algo_api.py        #   바이낸스 Algo Order REST 래퍼
    └── host.py            #   실행 믹스인이 기대하는 호스트 인터페이스 + TrailConfig
```

레이어를 세 가지 관심사로 갈라놨다 — **결정(strategy) / 측정(backtest) / 실행(execution)**. 셋은 [`TradingStrategy`](engine/strategy/protocol.py) 하나로만 맞물린다.

### 레이어별 역할

- **strategy** — 봉 데이터를 받아 시그널(`-1`/`0`/`+1`)을 내고, 자기 포지션의 청산 조건만 관리한다. 엔진 내부는 전혀 모른다.
- **backtest** — 전략 객체를 과거 데이터 위에서 봉 하나씩 전진시키며 트레이드를 기록하고 지표를 뽑는다. 매 봉 `close[:t+1]`만 넘겨서 룩어헤드를 막는 게 여기 핵심.
- **data** — OHLCV를 CSV에서 읽거나(`load_csv`), 키·네트워크 없이 돌릴 수 있게 합성 데이터를 만든다(`synth_ohlcv`). `cache`는 반복 다운로드를 줄이는 2단계 캐시.
- **execution** — 라이브 전용. 진입 후 SL/TP/트레일링을 거래소 서버(Algo Order)에 걸어, 봇이 떨어져도 리스크가 보호되게 한다. 트레일링 폭은 알파가 아니라 범용 `TrailConfig`로 뺐다.

> 백테스트↔실거래 일체화, 정합성(reconciliation), 리스크 가드 설계 등 더 자세한 내용은 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 참고.

## 데이터 흐름 (백테스트 한 봉)

```
매 봉 t 마다:
  update_market_data(symbol, high[:t+1], low[:t+1], close[:t+1])   # 마감된 봉만
  ├─ 포지션 없음 → on_bar() 로 시그널 확인, 시그널 있으면 open_position()
  └─ 포지션 있음 → update_position() 로 청산 조건 체크, 걸리면 close_position()
```

라이브 봇도 정확히 이 순서로 같은 메서드를 호출한다. 그래서 백테스트와 실거래가 갈라지지 않는다.

## 가이드

### 1. 실데이터로 백테스트 돌리기

`high/low/close` 배열만 있으면 된다. CSV(컬럼: `high,low,close`)에서 바로 읽을 수 있다.

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

`result.trades` 로 개별 트레이드(진입/청산 인덱스, 청산 사유, net 수익률)까지 까볼 수 있다.

### 2. 내 전략 붙이기

[`TradingStrategy`](engine/strategy/protocol.py) 인터페이스 메서드만 구현하면 백테스터·라이브 양쪽에서 그대로 돈다. 상속 필요 없고(덕 타이핑), 시그널은 `-1`(숏) / `0`(관망) / `+1`(롱) 규칙만 지키면 된다.

```python
import numpy as np

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
        ...  # 지표 계산에 쓸 배열 저장 (받는 배열엔 미래 봉이 없다)

    # ── 시그널 ──────────────────────────────────
    def on_bar(self, symbol, close) -> int:
        return 0  # -1 / 0 / +1

    def get_last_atr_pct(self, symbol) -> float:
        return 0.0  # 사이징용 ATR(가격 대비 비율)

    # ── 포지션 라이프사이클 ───────────────────────
    def open_position(self, symbol, signal, entry_price, atr_pct) -> None:
        self._positions[symbol] = {"signal": signal, "entry_price": entry_price}

    def update_position(self, symbol, price, high=None, low=None) -> str | None:
        return None  # 청산 사유 문자열(예: "stop_loss") 또는 None(보유 유지)

    def close_position(self, symbol) -> dict | None:
        return self._positions.pop(symbol, None)
```

구현 예시는 [`demo_squeeze.py`](engine/strategy/demo_squeeze.py)가 가장 짧은 레퍼런스다.

### 3. 라이브 실행 (선택)

거래소에 실제로 주문을 태우는 `engine.execution`은 `python-binance`가 필요하다.

```bash
uv pip install -e ".[live]"
cp .env.example .env   # 바이낸스 API 키 입력 (.env 는 커밋 금지)
```

`brackets.py`는 진입 직후 SL/TP/트레일링을 거래소 Algo Order로 걸고, 가격이 유리하게 움직이면 트레일링 스탑을 서버사이드로 래칫(한 방향으로만 조임)한다.

## 개발

```bash
uv run pytest -q        # 테스트 (네트워크·키 불필요)
uv run ruff check .     # 린트
```

## 라이선스

MIT
