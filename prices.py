"""Live price helpers (yfinance)."""

from __future__ import annotations

from typing import Iterable

import pandas as pd
import yfinance as yf


def fetch_last_prices(symbols: Iterable[str]) -> dict[str, float | None]:
    """Return last available close/last for each symbol.

    Uses a single multi-ticker download when possible, with per-symbol fallback.
    """
    symbols = [s.strip().upper() for s in symbols if s and str(s).strip()]
    if not symbols:
        return {}

    prices: dict[str, float | None] = {s: None for s in symbols}

    try:
        data = yf.download(
            tickers=" ".join(symbols),
            period="5d",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
    except Exception:
        data = None

    if data is not None and not data.empty:
        if len(symbols) == 1:
            sym = symbols[0]
            try:
                closes = data["Close"].dropna()
                if len(closes):
                    prices[sym] = float(closes.iloc[-1])
            except Exception:
                pass
        else:
            for sym in symbols:
                try:
                    if isinstance(data.columns, pd.MultiIndex):
                        if sym in data.columns.get_level_values(0):
                            series = data[sym]["Close"].dropna()
                            if len(series):
                                prices[sym] = float(series.iloc[-1])
                    elif "Close" in data.columns:
                        # Unexpected single-level frame for multi — skip to fallback
                        pass
                except Exception:
                    continue

    # Fallback: Ticker.info / history per missing symbol
    for sym in symbols:
        if prices.get(sym) is not None:
            continue
        try:
            t = yf.Ticker(sym)
            hist = t.history(period="5d")
            if hist is not None and not hist.empty and "Close" in hist.columns:
                prices[sym] = float(hist["Close"].dropna().iloc[-1])
                continue
            info = getattr(t, "fast_info", None)
            if info is not None:
                last = getattr(info, "last_price", None) or getattr(info, "lastPrice", None)
                if last is None and isinstance(info, dict):
                    last = info.get("last_price") or info.get("lastPrice")
                if last is not None:
                    prices[sym] = float(last)
        except Exception:
            prices[sym] = None

    return prices
