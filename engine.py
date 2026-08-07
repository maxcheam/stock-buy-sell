"""Orchestrate import → positions → prices → strategy recommendations."""

from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

import pandas as pd

from .prices import fetch_last_prices
from .stockevents import Position, derive_positions, load_stockevents_csv, positions_to_dataframe
from .strategy import StrategyRecommendation, recommend_for_position


def build_recommendations(
    trades: pd.DataFrame,
    prices: dict[str, float | None] | None = None,
    *,
    fetch_prices: bool = True,
) -> tuple[list[Position], list[StrategyRecommendation], pd.DataFrame]:
    """Return positions, recommendations, and a display dataframe."""
    positions = derive_positions(trades)
    symbols = [p.symbol for p in positions]

    if prices is None and fetch_prices:
        prices = fetch_last_prices(symbols)
    prices = prices or {}

    recs = [
        recommend_for_position(
            p.symbol,
            p.shares,
            p.avg_cost,
            prices.get(p.symbol),
        )
        for p in positions
    ]

    display = recommendations_to_dataframe(positions, recs)
    return positions, recs, display


def recommendations_to_dataframe(
    positions: list[Position],
    recs: list[StrategyRecommendation],
) -> pd.DataFrame:
    pos_map = {p.symbol: p for p in positions}
    rows = []
    for r in recs:
        p = pos_map.get(r.symbol)
        market_value = (
            (r.current_price * r.shares) if r.current_price is not None else None
        )
        unrealized = (
            (r.current_price - r.avg_cost) * r.shares
            if r.current_price is not None
            else None
        )
        rows.append(
            {
                "Symbol": r.symbol,
                "Shares": r.shares,
                "Avg Cost": r.avg_cost,
                "Current Price": r.current_price,
                "Change %": r.change_pct,
                "Market Value": market_value,
                "Unrealized P&L": unrealized,
                "Action": r.action,
                "Action %": r.action_pct,
                "Shares Δ": r.shares_delta,
                "Est. $ Impact": r.dollar_delta,
                "Rule": r.rule_label,
                "Rationale": r.rationale,
                "Next Level": r.next_level_label,
                "Next Level Price": r.next_level_price,
                "Trades": p.trade_count if p else 0,
                "First Trade": p.first_date if p else "",
                "Last Trade": p.last_date if p else "",
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty and "Action" in df.columns:
        df["_action_rank"] = df["Action"].map(_action_sort_key)
        df = df.sort_values(
            by=["_action_rank", "Change %"],
            ascending=[True, True],
            na_position="last",
        ).drop(columns=["_action_rank"]).reset_index(drop=True)
    return df


def _action_sort_key(action: str) -> int:
    order = {
        "SELL_ALL": 0,
        "SELL": 1,
        "ADD": 2,
        "HOLD": 3,
        "NO_PRICE": 4,
        "FLAT": 5,
    }
    return order.get(str(action), 9)


def action_summary(recs: Iterable[StrategyRecommendation]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in recs:
        counts[r.action] = counts.get(r.action, 0) + 1
    return counts


def load_and_recommend(path_or_buffer, *, fetch_prices: bool = True):
    trades = load_stockevents_csv(path_or_buffer)
    return trades, *build_recommendations(trades, fetch_prices=fetch_prices)


def recommendation_records(recs: list[StrategyRecommendation]) -> list[dict]:
    return [asdict(r) for r in recs]


# Re-export helpers used by the UI
__all__ = [
    "build_recommendations",
    "recommendations_to_dataframe",
    "action_summary",
    "load_and_recommend",
    "load_stockevents_csv",
    "derive_positions",
    "positions_to_dataframe",
    "fetch_last_prices",
]
