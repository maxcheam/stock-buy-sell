"""Unit tests for ladder strategy + position math (no network)."""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import pytest

from stock_buy_sell.positions import derive_positions, load_trades_csv
from stock_buy_sell.strategy import recommend_for_position


def test_drop_20_adds_10_percent():
    r = recommend_for_position("ABC", shares=100, avg_cost=100.0, current_price=80.0)
    assert r.action == "ADD"
    assert r.action_pct == 10.0
    assert abs(r.shares_delta - 10.0) < 1e-9
    assert r.change_pct == pytest.approx(-20.0)


def test_drop_10_holds():
    r = recommend_for_position("ABC", shares=100, avg_cost=100.0, current_price=90.0)
    assert r.action == "HOLD"
    assert r.shares_delta == 0.0


def test_drop_50_adds_50_percent():
    r = recommend_for_position("ABC", shares=100, avg_cost=100.0, current_price=50.0)
    assert r.action == "ADD"
    assert r.action_pct == 50.0
    assert abs(r.shares_delta - 50.0) < 1e-9


def test_rise_30_sells_10_percent():
    r = recommend_for_position("ABC", shares=100, avg_cost=100.0, current_price=130.0)
    assert r.action == "SELL"
    assert r.action_pct == 10.0
    assert abs(r.shares_delta + 10.0) < 1e-9


def test_rise_100_sells_all():
    r = recommend_for_position("ABC", shares=40, avg_cost=50.0, current_price=100.0)
    assert r.action == "SELL_ALL"
    assert r.action_pct == 100.0
    assert abs(r.shares_delta + 40.0) < 1e-9


def test_rise_20_holds():
    r = recommend_for_position("ABC", shares=100, avg_cost=100.0, current_price=120.0)
    assert r.action == "HOLD"


def test_within_band_holds():
    r = recommend_for_position("ABC", shares=100, avg_cost=100.0, current_price=105.0)
    assert r.action == "HOLD"
    assert "±10%" in r.rule_label or "Within" in r.rule_label


def test_highest_tier_wins_on_drop():
    # −45% should hit 40% tier (add 30%), not 30% or 50%
    r = recommend_for_position("ABC", shares=100, avg_cost=100.0, current_price=55.0)
    assert r.action == "ADD"
    assert r.action_pct == 30.0
    assert "40%" in r.rule_label


def test_flat_position():
    r = recommend_for_position("ABC", shares=0, avg_cost=0, current_price=10)
    assert r.action == "FLAT"


def test_average_cost_from_buys_and_sells():
    csv = """Symbol,Date,Quantity,Price,Price Currency,Fees Percentage,Fees Amount,Fees Currency
XYZ,2024-01-01,10.0,100.0,USD,0,0,USD
XYZ,2024-02-01,10.0,50.0,USD,0,0,USD
XYZ,2024-03-01,-5.0,80.0,USD,0,0,USD
"""
    trades = load_trades_csv(io.StringIO(csv))
    positions = derive_positions(trades)
    assert len(positions) == 1
    p = positions[0]
    assert p.symbol == "XYZ"
    assert p.shares == pytest.approx(15.0)
    # After two buys: 10@100 + 10@50 = $1500 / 20 = $75 avg
    # Sell 5 at avg 75 → remaining cost 15*75 = 1125 → avg still 75
    assert p.avg_cost == pytest.approx(75.0)


def test_sample_csv_loads_if_present():
    input_dir = Path(__file__).resolve().parent.parent / "input"
    sample = None
    for name in ("Orders_eTrade.csv", "Orders-etrade-papertrade.csv"):
        candidate = input_dir / name
        if candidate.exists():
            sample = candidate
            break
    if sample is None:
        pytest.skip("sample CSV not present")
    trades = load_trades_csv(sample)
    assert not trades.empty
    assert set(["Symbol", "Date", "Quantity", "Price"]).issubset(trades.columns)
    positions = derive_positions(trades)
    assert len(positions) >= 1
    for p in positions:
        assert p.shares > 0
