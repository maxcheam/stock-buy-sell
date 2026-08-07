"""Import broker / StockEvents transaction CSVs and derive open positions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

try:
    from .brokers import load_broker_csv, supported_brokers_table
except ImportError:  # flat deploy (Streamlit Cloud)
    from brokers import load_broker_csv, supported_brokers_table  # type: ignore


REQUIRED_COLUMNS = {
    "Symbol",
    "Date",
    "Quantity",
    "Price",
}


@dataclass(frozen=True)
class Position:
    symbol: str
    shares: float
    avg_cost: float
    cost_basis: float
    buy_shares: float
    sell_shares: float
    trade_count: int
    first_date: str
    last_date: str


# Last detected source label (for UI); set by load_stockevents_csv
_LAST_SOURCE: str = ""


def get_last_import_source() -> str:
    return _LAST_SOURCE


def load_stockevents_csv(path_or_buffer, *, broker: str | None = None) -> pd.DataFrame:
    """Load a transaction CSV from StockEvents or major brokers.

    Auto-detects format. Canonical convention:
      - Positive Quantity = buy / increase
      - Negative Quantity = sell / decrease
    """
    global _LAST_SOURCE
    out, source = load_broker_csv(path_or_buffer, broker=broker)
    _LAST_SOURCE = source
    for col in REQUIRED_COLUMNS:
        if col not in out.columns:
            raise ValueError(f"Internal error: missing {col} after import from {source}")
    if "Fees Amount" not in out.columns:
        out["Fees Amount"] = 0.0
    return out


def derive_positions(trades: pd.DataFrame) -> list[Position]:
    """Average-cost open lots per symbol (buys increase cost basis; sells reduce shares).

    Average cost is recalculated on buys only. Sells reduce share count and cost basis
    proportionally (average-cost method), which matches common retail portfolio apps.
    """
    if trades.empty:
        return []

    positions: list[Position] = []
    for symbol, g in trades.groupby("Symbol", sort=True):
        g = g.sort_values("Date")
        shares = 0.0
        cost_basis = 0.0
        buy_shares = 0.0
        sell_shares = 0.0

        for _, row in g.iterrows():
            qty = float(row["Quantity"])
            price = float(row["Price"])
            fees = float(row.get("Fees Amount", 0.0) or 0.0)

            if qty > 0:
                # Buy: add to cost basis (price * qty + fees)
                cost_basis += price * qty + abs(fees)
                shares += qty
                buy_shares += qty
            elif qty < 0:
                sell_qty = abs(qty)
                sell_shares += sell_qty
                if shares <= 1e-12:
                    # Short / oversell — clamp to flat
                    shares = 0.0
                    cost_basis = 0.0
                    continue
                sell_qty = min(sell_qty, shares)
                avg = cost_basis / shares if shares else 0.0
                cost_basis -= avg * sell_qty
                shares -= sell_qty
                if shares <= 1e-9:
                    shares = 0.0
                    cost_basis = 0.0

        if shares <= 1e-9:
            continue

        avg_cost = cost_basis / shares if shares else 0.0
        first = g["Date"].min()
        last = g["Date"].max()
        positions.append(
            Position(
                symbol=str(symbol),
                shares=round(shares, 8),
                avg_cost=float(avg_cost),
                cost_basis=float(cost_basis),
                buy_shares=float(buy_shares),
                sell_shares=float(sell_shares),
                trade_count=int(len(g)),
                first_date=first.strftime("%Y-%m-%d") if pd.notna(first) else "",
                last_date=last.strftime("%Y-%m-%d") if pd.notna(last) else "",
            )
        )
    return positions


def positions_to_dataframe(positions: list[Position]) -> pd.DataFrame:
    if not positions:
        return pd.DataFrame(
            columns=[
                "Symbol",
                "Shares",
                "Avg Cost",
                "Cost Basis",
                "Buy Shares",
                "Sell Shares",
                "Trades",
                "First Trade",
                "Last Trade",
            ]
        )
    rows = [
        {
            "Symbol": p.symbol,
            "Shares": p.shares,
            "Avg Cost": p.avg_cost,
            "Cost Basis": p.cost_basis,
            "Buy Shares": p.buy_shares,
            "Sell Shares": p.sell_shares,
            "Trades": p.trade_count,
            "First Trade": p.first_date,
            "Last Trade": p.last_date,
        }
        for p in positions
    ]
    return pd.DataFrame(rows)


def default_sample_path(root: Path | None = None) -> Path:
    """Locate the bundled sample CSV (dev tree, next to .exe, or PyInstaller extract)."""
    import os
    import sys

    name = "stock_events_transactions_2026-08-02.csv"
    candidates: list[Path] = []
    if root is not None:
        candidates.append(Path(root) / "input" / name)
    # Next to executable / project root (shareable with the .exe)
    user_dir = os.environ.get("STOCK_BUY_SELL_USER_DIR")
    if user_dir:
        candidates.append(Path(user_dir) / "input" / name)
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "input" / name)
        if hasattr(sys, "_MEIPASS"):
            candidates.append(Path(sys._MEIPASS) / "input" / name)
    # Dev layout
    candidates.append(Path(__file__).resolve().parent.parent / "input" / name)
    for path in candidates:
        if path.is_file():
            return path
    return candidates[0]
