"""Import StockEvents transaction CSVs and derive open positions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


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


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Map common StockEvents / export variants onto a standard schema."""
    rename: dict[str, str] = {}
    lower_map = {c: str(c).strip() for c in df.columns}
    df = df.rename(columns=lower_map)

    aliases = {
        "Symbol": ["symbol", "ticker", "Ticker", "SYMBOL", "Stock"],
        "Date": ["date", "Date", "Trade Date", "trade_date", "Datetime"],
        "Quantity": ["quantity", "Quantity", "Qty", "qty", "Shares", "shares"],
        "Price": ["price", "Price", "Fill Price", "fill_price", "Avg Price"],
        "Price Currency": ["Price Currency", "price currency", "Currency", "currency"],
        "Fees Amount": ["Fees Amount", "fees amount", "Fee", "Fees", "Commission"],
        "Fees Currency": ["Fees Currency", "fees currency"],
        "Fees Percentage": ["Fees Percentage", "fees percentage"],
    }

    cols_lower = {c.lower(): c for c in df.columns}
    for canonical, options in aliases.items():
        if canonical in df.columns:
            continue
        for opt in options:
            key = opt.lower()
            if key in cols_lower:
                rename[cols_lower[key]] = canonical
                break

    if rename:
        df = df.rename(columns=rename)
    return df


def load_stockevents_csv(path_or_buffer) -> pd.DataFrame:
    """Load and clean a StockEvents transactions export.

    Convention (StockEvents):
      - Positive Quantity = buy / increase
      - Negative Quantity = sell / decrease
    """
    df = pd.read_csv(path_or_buffer)
    df = _normalize_columns(df)

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(
            f"CSV is missing required columns: {sorted(missing)}. "
            f"Found: {list(df.columns)}"
        )

    out = df.copy()
    out["Symbol"] = out["Symbol"].astype(str).str.strip().str.upper()
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
    out["Quantity"] = pd.to_numeric(out["Quantity"], errors="coerce")
    out["Price"] = pd.to_numeric(out["Price"], errors="coerce")

    if "Fees Amount" in out.columns:
        out["Fees Amount"] = pd.to_numeric(out["Fees Amount"], errors="coerce").fillna(0.0)
    else:
        out["Fees Amount"] = 0.0

    out = out.dropna(subset=["Symbol", "Date", "Quantity", "Price"])
    out = out[out["Symbol"].str.len() > 0]
    out = out[out["Price"] >= 0]
    out = out.sort_values(["Symbol", "Date"]).reset_index(drop=True)
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
