# Stock Buy/Sell Ladder

Import **StockEvents** transaction history and apply the percentage ladder from
`input/Stock Buy Sell.png` to each open ticker.

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

## Run

```bash
# from workspace root
pip install -r requirements.txt
streamlit run stock_buy_sell/app.py
```

Windows: double-click `run_buy_sell.bat`.

### Share with friends on mobile (easiest)

Host the app online and send a **link** (phone browser / Add to Home Screen).  
Step-by-step: **[SHARE_WITH_FRIENDS.md](../SHARE_WITH_FRIENDS.md)** (Streamlit Cloud, free).

### Share as a Windows executable

Build a portable folder with an `.exe` (no Python install for recipients):

```bat
build_exe.bat
```

Then zip and share `dist\StockBuySellLadder\` (the **whole folder**, not only the `.exe`).

See [PACKAGING.md](PACKAGING.md) for details, size, and limitations.

## Input CSV (StockEvents)

Columns expected:

```
Symbol,Date,Quantity,Price,Price Currency,Fees Percentage,Fees Amount,Fees Currency
```

- Positive `Quantity` = buy  
- Negative `Quantity` = sell  

Sample: `input/stock_events_transactions_2026-08-02.csv`

## Programmatic use

```python
from stock_buy_sell.engine import load_and_recommend

trades, positions, recs, display = load_and_recommend(
    "input/stock_events_transactions_2026-08-02.csv"
)
print(display[["Symbol", "Change %", "Action", "Shares Δ", "Est. $ Impact", "Rule"]])
```

## Modules

| File | Role |
|------|------|
| `stockevents.py` | CSV import + average-cost positions |
| `strategy.py` | Ladder rule engine |
| `prices.py` | yfinance last prices |
| `engine.py` | End-to-end pipeline |
| `app.py` | Streamlit UI |
