# QuantBox Engine ⚙️

[![CI](https://github.com/younghwan91/quantbox-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/younghwan91/quantbox-engine/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-younghwan--chae-0A66C2?logo=linkedin&logoColor=white)](https://www.linkedin.com/in/younghwan-chae/)

**전략에 종속되지 않는 암호화폐 선물 백테스트·실행 엔진.** 백테스트와 실거래가 같은 코드를 타고, 룩어헤드(미래 데이터 참조)가 구조적으로 끼어들 수 없게 짰다.

직접 운용하는 바이낸스 USDT-M 선물 자동매매 시스템에서 전략(알파)만 비공개로 두고 인프라 레이어를 떼어내 공개한 저장소다. 같이 들어 있는 데모 전략은 교과서 로직(볼린저 밴드 스퀴즈)이고, 여기서 보여주려는 것은 "돈 버는 전략"이 아니라 **전략을 제대로 검증하고 실거래까지 안전하게 태우는 엔진**이다.

## 빠른 시작

필요한 건 Python 3.10+ 와 [uv](https://docs.astral.sh/uv/) 뿐이다. 백테스트는 `numpy` + `pandas` 만으로 돌아가고, 거래소에 실제 주문을 태울 때만 `python-binance` 가 더 필요하다(`.[live]`).

```bash
uv venv && uv pip install -e ".[dev]"

uv run qbe-backtest --demo      # 합성 데이터로 데모 백테스트
uv run pytest -q                # 테스트 (네트워크·API 키 불필요)
```

![데모 백테스트 실행 화면](docs/images/demo-backtest.png)

> ⚠️ 위 숫자는 **합성 데이터에 데모 전략을 돌린 결과**다. 엔진이 돈다는 것만 보여줄 뿐 수익성과 무관하다.

## 내 데이터·전략으로 써보기

`high/low/close` 배열만 있으면 실제 백테스트가 돌아간다.

```python
from engine.backtest.vectorized import run_backtest
from engine.data.klines import load_csv
from engine.strategy.demo_squeeze import SqueezeStrategy

high, low, close = load_csv("BTCUSDT_1h.csv")   # 컬럼: high,low,close
result = run_backtest(SqueezeStrategy(), high, low, close, fee=0.0004, slippage=0.0002)
print(result.summary())
# {'n_trades': ..., 'total_return': ..., 'win_rate': ..., 'profit_factor': ..., 'max_drawdown': ...}
```

내 전략을 붙이려면 [`TradingStrategy`](engine/strategy/protocol.py) 프로토콜(상속 불필요, 메서드만 맞추면 됨)만 구현하면 된다 — 방법과 라이브 실행까지는 [docs/USAGE.md](docs/USAGE.md) 참고.

## 핵심 설계

| 설계 | 어떻게 |
|---|---|
| **룩어헤드 원천 차단** | 백테스터가 매 봉마다 `close[:t+1]`(현재까지 마감된 봉)만 전략에 넘긴다. 전략이 받는 배열에 미래가 없으니 들여다볼 수가 없다. ([테스트로 검증](tests/test_backtest.py)) |
| **백테스트 = 실거래 동일 코드** | 같은 `TradingStrategy` 객체를 백테스터와 라이브 봇이 같은 순서로 호출한다. 재구현 괴리가 생길 자리가 없다. |
| **전략 플러그인 구조** | 엔진은 [`TradingStrategy`](engine/strategy/protocol.py)(PEP 544 프로토콜)로만 전략과 대화한다. 상속 없이 메서드만 맞추면 붙는다. |
| **비용 반영** | 테이커 수수료 + 슬리피지를 진입·청산 양쪽 레그에 매긴다. 나오는 수익률이 gross 가 아니라 net 이다. |
| **서버사이드 청산** | SL/TP·트레일링 스탑을 바이낸스 Algo Order 로 거래소에 걸어둔다. 봇이 죽어도 거래소가 청산한다. ([brackets.py](engine/execution/brackets.py)) |

## 구조

**결정(전략) / 측정(백테스트) / 실행** 을 갈라놓고 셋은 `TradingStrategy` 하나로만 맞물린다.

```mermaid
flowchart LR
    subgraph Data["engine/data/"]
        CSV[("OHLCV CSV\nload_csv()")]
        SYN[["synth_ohlcv()\n합성 데이터"]]
        CACHE["cache.py\n메모리+gzip 캐시"]
    end

    subgraph Decide["engine/strategy/ — 결정"]
        PROTO{{"TradingStrategy\n(PEP 544 프로토콜)"}}
        SQZ["demo_squeeze.py\nSqueezeStrategy"]
        PROTO -.구현.-> SQZ
    end

    CSV --> BT
    SYN --> BT
    CACHE -.캐시.-> CSV

    subgraph Measure["engine/backtest/ — 측정"]
        BT["vectorized.run_backtest()\n매 봉 close[:t+1]만 전달\n(룩어헤드 차단)"]
        RES["BacktestResult\ntrades · equity curve · MDD"]
        BT --> RES
    end

    BT <-->|"update_market_data / on_bar\nopen·update·close_position"| PROTO

    subgraph Execute["engine/execution/ — 실행 (.[live])"]
        HOST["host.py\nScalperProtocol (라이브 봇 host)"]
        BRACKET["brackets.py\nBracketMixin — SL/TP/트레일링"]
        ALGO["algo_api.py\nAlgoApiClient"]
        HOST --> BRACKET --> ALGO
    end

    PROTO ==같은 인터페이스\n(백테스트=실거래)==> HOST
    ALGO --> BINANCE[("Binance USDT-M\nFutures Algo Order API")]
```

- 전략 붙이는 법·라이브 실행까지 자세히 → [docs/USAGE.md](docs/USAGE.md)
- 룩어헤드 차단·실거래 일체화·비용 모델의 설계 근거 → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## 라이선스

MIT

---

버그·질문은 [Issues](https://github.com/younghwan91/quantbox-engine/issues)로.

**채영환 (Younghwan Chae)** · [GitHub @younghwan91](https://github.com/younghwan91) · [LinkedIn](https://www.linkedin.com/in/younghwan-chae/) — 다른 오픈소스 퀀트 프로젝트(한국·미국 주식, 암호화폐)는 [프로필](https://github.com/younghwan91)에서 볼 수 있습니다.
