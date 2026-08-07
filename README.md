# Stock Buy/Sell Ladder

Import transaction history from **major brokers**, then apply the percentage buy/sell ladder (from `input/Stock Buy Sell.png`) to every open ticker.

Live prices via **yfinance**. UI is a **Streamlit** app — run locally or host on Streamlit Cloud and share a link (works on phones).

## Features

- **Multi-broker CSV import** — format and title rows are auto-detected (see below)
- **Average-cost positions** from fill history (+buy / −sell)
- **Ladder recommendations** per ticker: HOLD / ADD / SELL / SELL ALL with share counts and estimated $ impact
- **Filter by stock** (multi-select + ticker search) and by action
- **Live prices** with refresh; charts for change-% and allocation
- **Download** recommendations as CSV

## Strategy (from screenshot)

**Reference price:** average cost of remaining shares.

| Drop from cost | Action | Rise from cost | Action |
|---:|:---|---:|:---|
| 10% | Hold | 10% | Hold |
| 20% | Add 10% of position | 20% | Hold |
| 30% | Add 30% | 30% | Sell 10% |
| 40% | Add 30% | 40% | Sell 20% |
| 50% | Add 50% | 50% | Sell 30% |
|  |  | 60% | Sell 40% |
|  |  | 100% | Sell everything |

Adds/sells are sized as a **percent of current open shares** at the live price.  
The **highest matching tier** wins (e.g. −35% maps to the 30% drop rule → add 30%).

## Supported CSV sources

Upload a **transaction history** or **orders** CSV. Format is **auto-detected**.

| Broker | Typical export |
|--------|----------------|
| Fidelity | Activity / Run Date + Action + Symbol |
| Charles Schwab | Transaction history |
| Interactive Brokers | Activity / Flex stock trades |
| Robinhood | Statement (Trans Code) |
| E*TRADE | Transaction history **or** Orders export (live/paper; filled stock only) |
| Webull / Moomoo | Fill / order export |
| TD Ameritrade / thinkorswim | Trade history |
| Vanguard | Transaction history |
| Generic | Symbol + Date + Qty + Price [+ Side], or signed qty (+buy / −sell) |

**Notes:**

- **Title/banner rows are auto-detected** on every upload (e.g. `Account … Orders, as of …`, “Generated for…”, multi-line preambles) — the real header is found by scoring column-name tokens  
- Equity **buys/sells only** — options, dividends, transfers, and similar rows are skipped when detected  
- Quantities are normalized to **positive = buy**, **negative = sell**  
- Detected broker name is shown after a successful import  

### Sample files (`input/`)

| File | Source |
|------|--------|
| `Orders_eTrade.csv` | **Real** E*TRADE account **Orders** export (live brokerage) |
| `Orders-etrade-papertrade.csv` | E*TRADE **paper trade** Orders export (mostly options in this sample) |

## Run locally

```bash
# from project root (folder that contains stock_buy_sell/ or the flat app files)
pip install -r requirements.txt
streamlit run stock_buy_sell/app.py
# or, if app.py is at repo root (Streamlit Cloud / flat layout):
# streamlit run app.py
```

Open the URL Streamlit prints (usually `http://localhost:8501`).

### Dependencies

```text
streamlit>=1.28
pandas>=2.0
numpy>=1.24
yfinance>=0.2.40
plotly>=5.18
matplotlib>=3.7
```

## Share with friends (mobile-friendly)

Host once on **[Streamlit Community Cloud](https://share.streamlit.io)** (free) and send a link.

1. Push this project to a **public GitHub** repo  
2. Deploy with main file: `app.py` (flat repo) or `stock_buy_sell/app.py` (package layout)  
3. Ensure **`requirements.txt`** is at the **repo root** and includes `plotly`  
4. Friends open the URL in a phone browser (optional: **Add to Home Screen**)

More detail: [SHARE_WITH_FRIENDS.md](../SHARE_WITH_FRIENDS.md)

**Tip:** After changing dependencies or imports, use Streamlit Cloud → **Manage app → Reboot** (a page refresh alone will not reinstall packages).

## Programmatic use

```python
from stock_buy_sell.engine import load_and_recommend

trades, positions, recs, display = load_and_recommend("input/Orders_eTrade.csv")
print(display[["Symbol", "Change %", "Action", "Shares Δ", "Est. $ Impact", "Rule"]])
```

Force a broker parser if auto-detect fails:

```python
from stock_buy_sell.positions import load_trades_csv

trades = load_trades_csv("my_export.csv", broker="fidelity")
```

## Modules

| File | Role |
|------|------|
| `app.py` | Streamlit UI (filters, tables, charts) |
| `brokers.py` | Multi-broker CSV detection, title skip & normalization |
| `positions.py` | Import wrapper + average-cost positions |
| `strategy.py` | Buy/sell ladder rule engine |
| `prices.py` | Live prices (yfinance) |
| `engine.py` | End-to-end pipeline |

## Layout notes (GitHub / Streamlit Cloud)

Two layouts are supported:

1. **Package:** `stock_buy_sell/app.py` + sibling modules under `stock_buy_sell/`  
2. **Flat repo root:** `app.py`, `brokers.py`, `engine.py`, `positions.py`, … next to each other  

Imports work in both cases. Keep `requirements.txt` at the **repository root**.

## Disclaimer

This tool is for **informational / educational** use only. It is **not financial advice**. Always do your own research and consider fees, taxes, and risk before trading.
