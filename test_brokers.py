"""Tests for multi-broker CSV import (no network)."""

from __future__ import annotations

import io

import pytest

from stock_buy_sell.brokers import detect_broker, load_broker_csv
from stock_buy_sell.positions import derive_positions, load_trades_csv


def _df_from(csv_text: str):
    trades, source = load_broker_csv(io.StringIO(csv_text))
    return trades, source


def test_simple_signed_qty_csv():
    csv = """Symbol,Date,Quantity,Price,Price Currency,Fees Percentage,Fees Amount,Fees Currency
AAPL,2024-06-06,10.0,100.0,USD,0,0,USD
AAPL,2024-07-01,-4.0,120.0,USD,0,0,USD
"""
    trades, source = _df_from(csv)
    assert "Generic" in source or "signed" in source.lower()
    assert len(trades) == 2
    assert trades.iloc[0]["Quantity"] == 10
    assert trades.iloc[1]["Quantity"] == -4
    pos = derive_positions(trades)
    assert len(pos) == 1
    assert pos[0].shares == pytest.approx(6.0)


def test_fidelity_buy_sell():
    csv = """Run Date,Action,Symbol,Description,Type,Quantity,Price ($),Commission,Fees,Amount
01/15/2024,YOU BOUGHT,MSFT,MICROSOFT CORP,Cash,5,400.00,0,0,-2000.00
02/20/2024,YOU SOLD,MSFT,MICROSOFT CORP,Cash,2,420.00,0,0,840.00
"""
    trades, source = _df_from(csv)
    assert "Fidelity" in source
    assert set(trades["Symbol"]) == {"MSFT"}
    buys = trades[trades["Quantity"] > 0]
    sells = trades[trades["Quantity"] < 0]
    assert buys["Quantity"].sum() == pytest.approx(5)
    assert sells["Quantity"].sum() == pytest.approx(-2)


def test_schwab_format():
    csv = """Date,Action,Symbol,Description,Quantity,Price,Fees & Comm,Amount
03/01/2024,Buy,NVDA,NVIDIA CORPORATION,10,500.00,0.00,-5000.00
04/01/2024,Sell,NVDA,NVIDIA CORPORATION,3,600.00,0.00,1800.00
"""
    trades, source = _df_from(csv)
    assert "Schwab" in source
    assert len(trades) == 2
    assert trades.loc[trades["Quantity"] > 0, "Quantity"].iloc[0] == 10
    assert trades.loc[trades["Quantity"] < 0, "Quantity"].iloc[0] == -3


def test_robinhood_format():
    csv = """Activity Date,Process Date,Settle Date,Instrument,Description,Trans Code,Quantity,Price,Amount
5/1/2024,5/1/2024,5/3/2024,TSLA,Tesla Inc,Buy,2,180.00,-360.00
5/10/2024,5/10/2024,5/12/2024,TSLA,Tesla Inc,Sell,1,200.00,200.00
"""
    trades, source = _df_from(csv)
    assert "Robinhood" in source
    assert len(trades) == 2


def test_ibkr_signed_qty():
    csv = """Symbol,Date/Time,Quantity,T,Price,Proceeds,Comm/Fee,Basis,Realized P/L,Code
AMD,2024-03-15 10:30:00,100,BUY,120.50,-12050,-1.0,12051,,O
AMD,2024-04-01 11:00:00,-40,SELL,140.00,5600,-1.0,,780,C
"""
    trades, source = _df_from(csv)
    assert "Interactive" in source or "ibkr" in source.lower() or "Generic" in source
    assert len(trades) == 2
    assert trades["Quantity"].sum() == pytest.approx(60)


def test_moomoo_direction():
    csv = """Code,Name,Direction,Fill Qty,Avg Price,Fill Time,Fee
US.AAPL,Apple,Buy,8,190.00,2024-06-01 09:35:00,0.5
US.AAPL,Apple,Sell,3,200.00,2024-07-01 10:00:00,0.5
"""
    trades, source = _df_from(csv)
    assert len(trades) >= 1
    # Symbol may be AAPL after clean
    assert any("AAPL" in s for s in trades["Symbol"].astype(str))


def test_generic_side_column():
    csv = """Ticker,Trade Date,Side,Shares,Fill Price
GOOG,2024-01-10,Buy,4,140.0
GOOG,2024-02-10,Sell,1,150.0
"""
    trades, source = _df_from(csv)
    assert len(trades) == 2
    assert trades["Symbol"].iloc[0] == "GOOG"


def test_skips_dividends():
    csv = """Date,Action,Symbol,Description,Quantity,Price,Amount
01/01/2024,Buy,KO,COCA COLA,10,60.00,-600
01/15/2024,Dividend,KO,COCA COLA DIVIDEND,,,15.00
"""
    trades, source = _df_from(csv)
    assert len(trades) == 1
    assert trades.iloc[0]["Symbol"] == "KO"


def test_load_trades_csv_wrapper():
    csv = """Symbol,Date,Quantity,Price
XOM,2024-01-01,5,100
"""
    trades = load_trades_csv(io.StringIO(csv))
    assert list(trades["Symbol"]) == ["XOM"]


def test_etrade_paper_stock_fills():
    """E*TRADE paper-trade Orders format with equity fills (options skipped)."""
    csv = '''"Paper Trade Account Orders, as of 08/06/26 at 09:33 PM EST"
"Symbol","Status","Fill","Description","Market","Time","Account"
"AAPL","Filled","10 @ 185.50","Buy 10 AAPL @ 185.50 Limit","185.40","08/01/2026, 9:35:00 AM","Paper Trade Account"
"AAPL","Filled","4 @ 190.00","Sell 4 AAPL @ 190 Limit","190.10","08/05/2026, 10:00:00 AM","Paper Trade Account"
"SPX","Filled","1 @ 5.40","Buy 1 Aug-31-26 7000/7200 Put Vertical @ 5.4 Limit","5.90","08/06/2026, 9:46:57 AM","Paper Trade Account"
"NKE","Open","--","Sell 2 Jul-31-26 44 Calls @ 0.16 Limit to Open","--","07/28/2026, 9:31:36 AM","Paper Trade Account"
"MSFT","Canceled","--","Buy 5 MSFT @ 400 Limit","400.00","07/23/2026, 9:54:18 AM","Paper Trade Account"
'''
    trades, source = _df_from(csv)
    assert "E*TRADE" in source
    assert set(trades["Symbol"]) == {"AAPL"}
    assert len(trades) == 2
    assert trades.loc[trades["Quantity"] > 0, "Quantity"].iloc[0] == pytest.approx(10)
    assert trades.loc[trades["Quantity"] < 0, "Quantity"].iloc[0] == pytest.approx(-4)
    assert trades.loc[trades["Quantity"] > 0, "Price"].iloc[0] == pytest.approx(185.50)


def test_etrade_paper_sample_file_options_only():
    """Paper-trade sample is options-heavy → clear error, not a crash."""
    from pathlib import Path

    sample = (
        Path(__file__).resolve().parent.parent
        / "input"
        / "Orders-etrade-papertrade.csv"
    )
    if not sample.exists():
        pytest.skip("sample file not present")
    with pytest.raises(ValueError, match="stock|option|Orders|E\\*TRADE"):
        load_broker_csv(sample)


def test_etrade_live_orders_sample_file():
    """Real E*TRADE account Orders export (input/Orders_eTrade.csv)."""
    from pathlib import Path

    sample = Path(__file__).resolve().parent.parent / "input" / "Orders_eTrade.csv"
    if not sample.exists():
        pytest.skip("sample file not present")
    trades, source = load_broker_csv(sample)
    assert "E*TRADE" in source
    assert not trades.empty
    assert set(trades["Symbol"]).issubset({"NFLX", "NKE"})
    # Only Filled equity rows; net qty may be partial lots
    assert (trades["Quantity"] != 0).all()


def test_etrade_paper_detect():
    csv = '''"Paper Trade Account Orders, as of 08/06/26"
"Symbol","Status","Fill","Description","Market","Time","Account"
"AAPL","Filled","1 @ 100","Buy 1 AAPL @ 100 Limit","100","08/01/2026, 9:00:00 AM","Paper Trade Account"
'''
    from stock_buy_sell.brokers import _read_csv_smart, _read_text, detect_broker

    text = _read_text(io.StringIO(csv))
    # Auto title detection (no explicit hints)
    df = _read_csv_smart(text)
    assert "Symbol" in df.columns
    assert detect_broker(df) == "etrade_orders"


def test_title_detection_generic_banner():
    """Any broker-style title line above headers should be skipped."""
    from stock_buy_sell.brokers import detect_title_skip_rows, load_broker_csv

    csv = '''My Broker Portfolio Export - Generated 2026-08-01 for Account 12345
Symbol,Date,Quantity,Price
AAPL,2024-01-15,10,150.00
AAPL,2024-02-01,-3,160.00
'''
    assert detect_title_skip_rows(csv) == 1
    trades, source = load_broker_csv(io.StringIO(csv))
    assert len(trades) == 2
    assert trades.iloc[0]["Symbol"] == "AAPL"


def test_title_detection_no_banner():
    """Normal CSVs with headers on row 0 still work."""
    from stock_buy_sell.brokers import detect_title_skip_rows

    csv = """Symbol,Date,Quantity,Price
MSFT,2024-01-01,5,300
"""
    assert detect_title_skip_rows(csv) == 0


def test_title_detection_fidelity_style_preamble():
    csv = '''Brokerage Account
Account Number: X123
Run Date,Action,Symbol,Description,Type,Quantity,Price ($),Commission,Fees,Amount
01/15/2024,YOU BOUGHT,MSFT,MICROSOFT CORP,Cash,5,400.00,0,0,-2000.00
'''
    from stock_buy_sell.brokers import detect_title_skip_rows, load_broker_csv

    assert detect_title_skip_rows(csv) >= 2
    trades, source = load_broker_csv(io.StringIO(csv))
    assert "Fidelity" in source or len(trades) >= 1
    assert trades.iloc[0]["Symbol"] == "MSFT"


def test_live_etrade_orders_title_auto():
    from pathlib import Path
    from stock_buy_sell.brokers import detect_title_skip_rows, _read_csv_smart, _read_text

    sample = Path(__file__).resolve().parent.parent / "input" / "Orders_eTrade.csv"
    if not sample.exists():
        pytest.skip("sample not present")
    text = _read_text(sample)
    assert detect_title_skip_rows(text) == 1
    df = _read_csv_smart(text)
    assert list(df.columns)[:4] == ["Symbol", "Status", "Fill", "Description"]
