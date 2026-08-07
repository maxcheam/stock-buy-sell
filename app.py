"""
Stock Buy/Sell Ladder — StockEvents import + strategy recommendations.

Strategy source: input/Stock Buy Sell.png
  • On drops from avg cost: hold / add 10–50% of position
  • On rises from avg cost: hold / sell 10–100% of position

Run:
  streamlit run stock_buy_sell/app.py
  or double-click run_buy_sell.bat
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

# Allow `streamlit run stock_buy_sell/app.py` without installing the package.
# When frozen (PyInstaller), prefer the extract dir + folder next to the .exe.
def _resolve_root() -> Path:
    if getattr(sys, "frozen", False):
        if hasattr(sys, "_MEIPASS"):
            return Path(sys._MEIPASS)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


_ROOT = _resolve_root()
_USER_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else _ROOT
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_USER_ROOT) not in sys.path:
    sys.path.insert(0, str(_USER_ROOT))

from stock_buy_sell.engine import (  # noqa: E402
    action_summary,
    build_recommendations,
    load_stockevents_csv,
)
from stock_buy_sell.stockevents import default_sample_path, positions_to_dataframe  # noqa: E402
from stock_buy_sell.strategy import ladder_reference_table  # noqa: E402

st.set_page_config(
    page_title="Stock Buy/Sell Ladder",
    page_icon="📉",
    layout="wide",
    initial_sidebar_state="expanded",
)

ACTION_COLORS = {
    "SELL_ALL": "#b91c1c",
    "SELL": "#dc2626",
    "ADD": "#16a34a",
    "HOLD": "#64748b",
    "NO_PRICE": "#a855f7",
    "FLAT": "#94a3b8",
}


def _fmt_money(x) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    return f"${x:,.2f}"


def _fmt_pct(x) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    return f"{x:+.1f}%"


def _fmt_shares(x) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    if abs(x - round(x)) < 1e-6:
        return f"{int(round(x))}"
    return f"{x:.4g}"


def _load_trades_from_upload(uploaded) -> pd.DataFrame:
    raw = uploaded.getvalue()
    return load_stockevents_csv(io.BytesIO(raw))


@st.cache_data(show_spinner="Fetching live prices…", ttl=120)
def _cached_build(trades_csv_bytes: bytes, mtime_key: str):
    trades = load_stockevents_csv(io.BytesIO(trades_csv_bytes))
    positions, recs, display = build_recommendations(trades, fetch_prices=True)
    return trades, positions, recs, display


def main() -> None:
    st.title("📉 Stock Buy/Sell Ladder")
    st.caption(
        "Import StockEvents transactions · evaluate each ticker against the "
        "drop-add / rise-sell percentage ladder · recommended shares & prices"
    )

    sample_path = default_sample_path(_USER_ROOT)
    if not sample_path.exists():
        sample_path = default_sample_path(_ROOT)

    with st.sidebar:
        st.header("Data")
        uploaded = st.file_uploader(
            "StockEvents transactions CSV",
            type=["csv"],
            help=(
                "Export from StockEvents: Symbol, Date, Quantity, Price, … "
                "Positive qty = buy, negative = sell."
            ),
        )
        use_sample = st.checkbox(
            "Use sample file from input/",
            value=uploaded is None and sample_path.exists(),
            disabled=not sample_path.exists(),
        )
        st.divider()
        st.header("Strategy")
        st.markdown(
            "Reference: **avg cost** of open shares (average-cost method).\n\n"
            "Adds/sells are **% of current position size** at the live price."
        )
        with st.expander("Full ladder rules", expanded=False):
            st.dataframe(
                pd.DataFrame(ladder_reference_table()),
                hide_index=True,
                use_container_width=True,
            )
        st.divider()
        if st.button("🔄 Refresh prices", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    # Resolve trades source
    trades_bytes: bytes | None = None
    source_label = ""
    if uploaded is not None:
        trades_bytes = uploaded.getvalue()
        source_label = uploaded.name
    elif use_sample and sample_path.exists():
        trades_bytes = sample_path.read_bytes()
        source_label = str(sample_path.relative_to(_ROOT))
    else:
        st.info(
            "Upload a StockEvents CSV in the sidebar, or place a sample at "
            f"`{sample_path}`."
        )
        st.stop()

    mtime_key = source_label
    try:
        trades, positions, recs, display = _cached_build(trades_bytes, mtime_key)
    except Exception as exc:
        st.error(f"Failed to process CSV: {exc}")
        st.stop()

    st.success(f"Loaded **{len(trades)}** transactions from `{source_label}` → **{len(positions)}** open positions")

    # --- Summary metrics ---
    counts = action_summary(recs)
    total_cost = sum(p.cost_basis for p in positions)
    total_mv = sum(
        (r.current_price * r.shares)
        for r in recs
        if r.current_price is not None
    )
    total_pnl = total_mv - total_cost if total_mv else None
    buy_cash = sum(r.dollar_delta for r in recs if r.action == "ADD")
    sell_cash = -sum(r.dollar_delta for r in recs if r.action in ("SELL", "SELL_ALL"))

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Open positions", len(positions))
    c2.metric("Cost basis", _fmt_money(total_cost))
    c3.metric("Market value", _fmt_money(total_mv) if total_mv else "—")
    c4.metric(
        "Unrealized P&L",
        _fmt_money(total_pnl) if total_pnl is not None else "—",
        delta=_fmt_pct((total_pnl / total_cost * 100) if total_cost and total_pnl is not None else None),
    )
    c5.metric("Recommended buys", _fmt_money(buy_cash), help="Est. cash to deploy on ADD signals")
    c6.metric("Recommended sells", _fmt_money(sell_cash), help="Est. cash from SELL signals")

    # Action chips
    chip_cols = st.columns(len(ACTION_COLORS))
    for col, (action, color) in zip(chip_cols, ACTION_COLORS.items()):
        n = counts.get(action, 0)
        col.markdown(
            f"<div style='background:{color}22;border:1px solid {color};"
            f"border-radius:8px;padding:8px;text-align:center'>"
            f"<b style='color:{color}'>{action}</b><br>{n}</div>",
            unsafe_allow_html=True,
        )

    tab_recs, tab_holds, tab_tx, tab_rules = st.tabs(
        ["🎯 Recommendations", "📦 Holdings", "📜 Transactions", "📖 Strategy rules"]
    )

    with tab_recs:
        if display.empty:
            st.warning("No open positions to evaluate.")
        else:
            all_symbols = sorted(display["Symbol"].astype(str).unique().tolist())
            all_actions = sorted(display["Action"].unique().tolist())

            f1, f2, f3 = st.columns([2, 2, 1])
            with f1:
                filter_symbols = st.multiselect(
                    "Filter by stock",
                    options=all_symbols,
                    default=[],
                    placeholder="All stocks (pick tickers to narrow)",
                    help="Leave empty to show every open position. Select one or more tickers to filter.",
                    key="rec_filter_symbols",
                )
            with f2:
                filter_actions = st.multiselect(
                    "Filter by action",
                    options=all_actions,
                    default=all_actions,
                    key="rec_filter_actions",
                )
            with f3:
                symbol_search = st.text_input(
                    "Search ticker",
                    value="",
                    placeholder="e.g. NVDA",
                    help="Case-insensitive substring match on symbol.",
                    key="rec_symbol_search",
                ).strip().upper()

            view = display.copy()
            if filter_symbols:
                view = view[view["Symbol"].isin(filter_symbols)]
            if filter_actions:
                view = view[view["Action"].isin(filter_actions)]
            else:
                view = view.iloc[0:0]
            if symbol_search:
                view = view[view["Symbol"].astype(str).str.contains(symbol_search, case=False, na=False)]

            st.caption(f"Showing **{len(view)}** of **{len(display)}** positions")

            # Pretty display table
            show = view[
                [
                    "Symbol",
                    "Shares",
                    "Avg Cost",
                    "Current Price",
                    "Change %",
                    "Market Value",
                    "Unrealized P&L",
                    "Action",
                    "Action %",
                    "Shares Δ",
                    "Est. $ Impact",
                    "Rule",
                    "Next Level",
                    "Next Level Price",
                ]
            ].copy()

            st.dataframe(
                show.style.format(
                    {
                        "Shares": lambda x: _fmt_shares(x),
                        "Avg Cost": _fmt_money,
                        "Current Price": _fmt_money,
                        "Change %": _fmt_pct,
                        "Market Value": _fmt_money,
                        "Unrealized P&L": _fmt_money,
                        "Action %": lambda x: f"{x:.0f}%" if pd.notna(x) else "—",
                        "Shares Δ": lambda x: _fmt_shares(x),
                        "Est. $ Impact": _fmt_money,
                        "Next Level Price": _fmt_money,
                    },
                    na_rep="—",
                ),
                use_container_width=True,
                hide_index=True,
                height=min(48 + 36 * max(len(show), 1), 720),
            )

            # Per-ticker detail cards for actionable rows (respect stock filter)
            visible_symbols = set(view["Symbol"].astype(str).tolist())
            actionable = [
                r
                for r in recs
                if r.action in ("ADD", "SELL", "SELL_ALL") and r.symbol in visible_symbols
            ]
            if actionable:
                st.subheader("Actionable tickers")
                for r in actionable:
                    color = ACTION_COLORS.get(r.action, "#64748b")
                    with st.expander(
                        f"{r.symbol} · {r.rule_label} · {_fmt_pct(r.change_pct)} vs cost",
                        expanded=len(visible_symbols) <= 3,
                    ):
                        a, b, c, d = st.columns(4)
                        a.metric("Shares held", _fmt_shares(r.shares))
                        b.metric("Avg cost", _fmt_money(r.avg_cost))
                        c.metric("Live price", _fmt_money(r.current_price))
                        d.metric("Change vs cost", _fmt_pct(r.change_pct))

                        if r.action == "ADD":
                            st.markdown(
                                f"<p style='color:{color};font-size:1.1rem'>"
                                f"<b>BUY</b> ≈ <b>{_fmt_shares(r.shares_delta)}</b> shares "
                                f"({r.action_pct:.0f}% of position) "
                                f"@ ~{_fmt_money(r.current_price)} "
                                f"→ est. <b>{_fmt_money(r.dollar_delta)}</b></p>",
                                unsafe_allow_html=True,
                            )
                        else:
                            st.markdown(
                                f"<p style='color:{color};font-size:1.1rem'>"
                                f"<b>SELL</b> ≈ <b>{_fmt_shares(abs(r.shares_delta))}</b> shares "
                                f"({r.action_pct:.0f}% of position) "
                                f"@ ~{_fmt_money(r.current_price)} "
                                f"→ est. proceeds <b>{_fmt_money(abs(r.dollar_delta))}</b></p>",
                                unsafe_allow_html=True,
                            )
                        st.write(r.rationale)
                        if r.next_level_label:
                            nxt = r.next_level_label
                            if r.next_level_price is not None:
                                nxt += f" (price ≈ {_fmt_money(r.next_level_price)})"
                            st.caption(f"Next ladder step: {nxt}")

            # Charts (filtered)
            chart_df = view.dropna(subset=["Change %"]).copy()
            if not chart_df.empty:
                st.subheader("Change % vs avg cost")
                fig = px.bar(
                    chart_df,
                    x="Symbol",
                    y="Change %",
                    color="Action",
                    color_discrete_map=ACTION_COLORS,
                    hover_data={
                        "Avg Cost": ":.2f",
                        "Current Price": ":.2f",
                        "Shares": True,
                        "Rule": True,
                    },
                )
                fig.add_hline(y=0, line_dash="dot", line_color="#94a3b8")
                fig.update_layout(
                    height=420,
                    margin=dict(l=20, r=20, t=30, b=20),
                    legend_title_text="Action",
                )
                st.plotly_chart(fig, use_container_width=True)

            csv_bytes = show.to_csv(index=False).encode("utf-8")
            st.download_button(
                "⬇️ Download recommendations CSV",
                data=csv_bytes,
                file_name="buy_sell_recommendations.csv",
                mime="text/csv",
            )

    with tab_holds:
        pos_df = positions_to_dataframe(positions)
        if pos_df.empty:
            st.info("No open holdings.")
        else:
            hold_symbols = sorted(pos_df["Symbol"].astype(str).unique().tolist())
            hold_filter = st.multiselect(
                "Filter by stock",
                options=hold_symbols,
                default=[],
                placeholder="All stocks",
                key="hold_filter_symbols",
            )
            hold_view = pos_df[pos_df["Symbol"].isin(hold_filter)] if hold_filter else pos_df
            st.caption(f"Showing **{len(hold_view)}** of **{len(pos_df)}** holdings")
            st.dataframe(
                hold_view.style.format(
                    {
                        "Shares": lambda x: _fmt_shares(x),
                        "Avg Cost": _fmt_money,
                        "Cost Basis": _fmt_money,
                        "Buy Shares": lambda x: _fmt_shares(x),
                        "Sell Shares": lambda x: _fmt_shares(x),
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )
            if not display.empty and display["Market Value"].notna().any():
                pie = display.dropna(subset=["Market Value"]).copy()
                if hold_filter:
                    pie = pie[pie["Symbol"].isin(hold_filter)]
                pie = pie[pie["Market Value"] > 0]
                if not pie.empty:
                    fig = px.pie(
                        pie,
                        names="Symbol",
                        values="Market Value",
                        title="Allocation by live market value",
                        hole=0.35,
                    )
                    fig.update_layout(height=420, margin=dict(l=20, r=20, t=40, b=20))
                    st.plotly_chart(fig, use_container_width=True)

    with tab_tx:
        show_tx = trades.copy()
        show_tx["Side"] = show_tx["Quantity"].apply(lambda q: "BUY" if q > 0 else "SELL")
        show_tx["Notional"] = show_tx["Quantity"].abs() * show_tx["Price"]
        tx_symbols = sorted(show_tx["Symbol"].astype(str).unique().tolist())
        tx_filter = st.multiselect(
            "Filter by stock",
            options=tx_symbols,
            default=[],
            placeholder="All stocks",
            key="tx_filter_symbols",
        )
        tx_view = show_tx[show_tx["Symbol"].isin(tx_filter)] if tx_filter else show_tx
        st.caption(f"Showing **{len(tx_view)}** of **{len(show_tx)}** transactions")
        st.dataframe(
            tx_view[
                ["Date", "Symbol", "Side", "Quantity", "Price", "Notional", "Fees Amount"]
            ].sort_values(["Date", "Symbol"], ascending=[False, True]),
            use_container_width=True,
            hide_index=True,
            height=520,
        )

    with tab_rules:
        st.markdown(
            """
### Unwritten rule for the stock market

Evaluated **per ticker** using **% change from average cost** of the open position
and the live market price.

#### On drops
| If price drops… | Do this |
|---:|:---|
| 10% | Just hold |
| 20% | **Add 10%** of current shares |
| 30% | **Add 30%** of current shares |
| 40% | **Add 30%** of current shares |
| 50% | **Add 50%** of current shares |

#### On rises
| If price rises… | Do this |
|---:|:---|
| 10% | Just hold |
| 20% | Still hold |
| 30% | **Sell 10%** of current shares |
| 40% | **Sell 20%** of current shares |
| 50% | **Sell 30%** of current shares |
| 60% | **Sell 40%** of current shares |
| 100% | **Sell everything** |

#### How this app applies the rules
1. Import StockEvents fills (buys positive, sells negative).
2. Build **average-cost** open lots per symbol.
3. Fetch **live prices** (yfinance).
4. Pick the **highest matching tier** for the current % move.
5. Size the order as **% of current shares** at the live price.

**Not financial advice.** Mechanical rule illustration only — size risk carefully
and consider taxes, fees, and fundamentals before trading.
            """
        )
        st.dataframe(
            pd.DataFrame(ladder_reference_table()),
            hide_index=True,
            use_container_width=True,
        )


if __name__ == "__main__":
    main()
