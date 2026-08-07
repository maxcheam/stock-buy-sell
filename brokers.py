"""Multi-broker transaction CSV import → canonical StockEvents-like schema.

Canonical output columns:
  Symbol, Date, Quantity, Price, Fees Amount, Source
  Quantity: +buy / −sell (shares)

Supported (auto-detected by headers):
  StockEvents, Fidelity, Charles Schwab, Interactive Brokers (Activity/Flex-ish),
  Robinhood, E*TRADE, Webull, Moomoo, TD Ameritrade / thinkorswim, Vanguard,
  and a generic Symbol/Date/Qty/Price (+ optional Side/Action) fallback.
"""

from __future__ import annotations

import io
import re
from typing import Any, BinaryIO, TextIO

import pandas as pd

CanonicalBuffer = str | bytes | BinaryIO | TextIO | io.BytesIO | io.StringIO | Any

CANONICAL_COLS = ["Symbol", "Date", "Quantity", "Price", "Fees Amount", "Source"]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _read_text(path_or_buffer: CanonicalBuffer) -> str:
    if hasattr(path_or_buffer, "read"):
        raw = path_or_buffer.read()
        if isinstance(raw, bytes):
            return raw.decode("utf-8-sig", errors="replace")
        return str(raw)
    if isinstance(path_or_buffer, bytes):
        return path_or_buffer.decode("utf-8-sig", errors="replace")
    path = str(path_or_buffer)
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        return f.read()


def _money(val) -> float | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if s in ("", "-", "--", "N/A", "n/a", "nan", "None", "null"):
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    s = s.replace("$", "").replace(",", "").replace("USD", "").replace("€", "").strip()
    s = re.sub(r"\s+", "", s)
    if s.startswith("+"):
        s = s[1:]
    try:
        v = float(s)
    except ValueError:
        m = re.search(r"-?[\d.]+", s)
        if not m:
            return None
        v = float(m.group(0))
    return -abs(v) if neg else v


def _qty(val) -> float | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().lower().replace(",", "")
    s = re.sub(r"\b(unit\(s\)|units|shares?|sh)\b", "", s).strip()
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        m = re.search(r"-?[\d.]+", s)
        return float(m.group(0)) if m else None


def _norm_header(h: str) -> str:
    h = str(h).replace("\ufeff", "").strip().lower()
    h = h.replace("_", " ").replace("-", " ")
    h = re.sub(r"[^\w\s/%&]", " ", h)
    h = re.sub(r"\s+", " ", h).strip()
    return h


def _cols_map(df: pd.DataFrame) -> dict[str, str]:
    """normalized header → original column name"""
    return {_norm_header(c): c for c in df.columns}


def _find_col(cmap: dict[str, str], *candidates: str) -> str | None:
    """Resolve a logical field to a real column name.

    Exact match first, then safe contains match. Candidates shorter than 3
    characters (e.g. IBKR ``T``) only match exactly — never as a substring of
    ``date`` / ``quantity``.
    """
    for cand in candidates:
        key = _norm_header(cand)
        if key in cmap:
            return cmap[key]
    for cand in candidates:
        key = _norm_header(cand)
        if not key or len(key) < 3:
            continue
        for nk, orig in cmap.items():
            if key == nk:
                return orig
            # whole-token style: "trade date" contains "date" as a word
            tokens = nk.split()
            if key in tokens or any(t == key for t in tokens):
                return orig
            # longer keys may be substrings: "price ($)" vs "price"
            if len(key) >= 4 and (key in nk or nk in key):
                return orig
    return None


def _side_sign(side_val) -> float | None:
    """Return +1 buy, -1 sell, None unknown."""
    if side_val is None or (isinstance(side_val, float) and pd.isna(side_val)):
        return None
    s = str(side_val).strip().lower()
    if not s:
        return None
    # Order matters: "buy to cover" is still buy, "sell short" is sell
    buy_tokens = (
        "buy", "bought", "purchase", "bot", "bto", "btc", "long",
        "you bought", "reinvest", "dividend reinvestment", "contribution",
    )
    sell_tokens = (
        "sell", "sold", "sld", "sto", "stc", "short", "you sold",
        "redemption", "withdrawal",
    )
    for t in sell_tokens:
        if t in s:
            return -1.0
    for t in buy_tokens:
        if t in s:
            return 1.0
    if s in ("b", "buy", "1"):
        return 1.0
    if s in ("s", "sell", "-1"):
        return -1.0
    return None


def _looks_like_option(symbol: str, description: str = "") -> bool:
    sym = (symbol or "").upper().strip()
    desc = (description or "").upper()
    if not sym:
        return True
    # OCC-ish: AAPL250117C00150000
    if re.match(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$", sym.replace(" ", "")):
        return True
    if re.search(r"\d{6}[CP]\d", sym.replace(" ", "")):
        return True
    if any(k in desc for k in (" CALL", " PUT", "CALL ", "PUT ", "OPTION")):
        # allow if symbol is plain equity and desc is something else — keep conservative
        if re.search(r"\b(CALL|PUT)\b", desc) and not re.match(r"^[A-Z]{1,5}$", sym):
            return True
        if re.search(r"\b(CALL|PUT)\b", desc) and re.search(r"\d", desc):
            return True
    # Moomoo/IB multi-leg slash
    if "/" in sym and re.search(r"[CP]", sym):
        return True
    return False


def _clean_symbol(val) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    s = str(val).strip().upper()
    # Fidelity sometimes prefixes
    s = s.replace("**", "").strip()
    # Strip exchange prefixes: NYSE:AAPL, US.AAPL, SMART:AMD
    if ":" in s:
        s = s.split(":")[-1]
    if re.match(r"^(US|HK|SH|SZ|JP)\.", s):
        s = s.split(".", 1)[-1]
    s = re.sub(r"\.(US|NYSE|NASDAQ|AMEX)$", "", s)
    # Drop pure cash / meta
    if s in ("USD", "CASH", "SPAXX", "FCASH", "CUR:USD", "PENDING", "NAN", "NONE", ""):
        return ""
    return s


def _is_trade_action(action: str) -> bool:
    s = (action or "").strip().lower()
    if not s:
        return True  # no action column → keep
    noise = (
        "dividend", "interest", "fee", "tax", "transfer", "journal",
        "wire", "deposit", "withdraw", "credit", "margin", "adjustment",
        "expired", "assigned", "exercise", "spin", "merge", "split only",
        "funds received", "funds sent", "ach", "card", "reward",
        "courtesy", "reorg", "name change", "security swap",
    )
    # Keep buy/sell even if description has "dividend" in other cols
    if _side_sign(s) is not None:
        if any(n in s for n in ("expired", "assigned", "exercise")):
            return False
        return True
    # Token / phrase noise (avoid bare substring traps like "fee" inside other words)
    for n in noise:
        if n in s.split() or n == s or f" {n} " in f" {s} " or s.startswith(n + " ") or s.endswith(" " + n):
            return False
        if len(n) >= 5 and n in s:
            return False
    # Unknown action text: keep the row (signed qty / other columns may still apply)
    return True


def _finalize(rows: list[dict], source: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=CANONICAL_COLS)
    df = pd.DataFrame(rows)
    df["Symbol"] = df["Symbol"].astype(str).str.strip().str.upper()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce")
    df["Price"] = pd.to_numeric(df["Price"], errors="coerce")
    df["Fees Amount"] = pd.to_numeric(df.get("Fees Amount", 0), errors="coerce").fillna(0.0)
    df["Source"] = source
    df = df.dropna(subset=["Symbol", "Date", "Quantity", "Price"])
    df = df[df["Symbol"].str.len() > 0]
    df = df[df["Quantity"] != 0]
    df = df[df["Price"] >= 0]
    # Drop obvious options
    mask_opt = df.apply(
        lambda r: _looks_like_option(str(r["Symbol"]), str(r.get("Description", ""))),
        axis=1,
    )
    df = df.loc[~mask_opt].copy()
    keep = [c for c in CANONICAL_COLS if c in df.columns]
    df = df[keep].sort_values(["Symbol", "Date"]).reset_index(drop=True)
    return df


def _find_header_line(text: str, must_have: tuple[str, ...]) -> int:
    """Return 0-based line index of header row, or 0."""
    lines = text.splitlines()
    for i, line in enumerate(lines[:40]):
        low = line.lower()
        if all(m.lower() in low for m in must_have):
            return i
    return 0


def _read_csv_smart(text: str, header_hints: tuple[str, ...] | None = None) -> pd.DataFrame:
    skip = 0
    if header_hints:
        skip = _find_header_line(text, header_hints)
    try:
        df = pd.read_csv(io.StringIO(text), skiprows=skip, dtype=str, keep_default_na=False)
    except Exception:
        df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    # Drop fully empty columns
    df = df.dropna(axis=1, how="all")
    # Strip column names
    df.columns = [str(c).replace("\ufeff", "").strip() for c in df.columns]
    # Drop unnamed empty
    df = df.loc[:, [c for c in df.columns if not str(c).startswith("Unnamed")]]
    return df


# ---------------------------------------------------------------------------
# format detection
# ---------------------------------------------------------------------------

def detect_broker(df: pd.DataFrame) -> str:
    cols = {_norm_header(c) for c in df.columns}
    joined = " | ".join(sorted(cols))

    def has(*names: str) -> bool:
        return all(any(n == c or n in c for c in cols) for n in names)

    # StockEvents exact-ish
    if has("symbol", "quantity", "price") and has("date") and (
        "fees amount" in cols or "fees percentage" in cols or "price currency" in cols
    ):
        return "stockevents"

    # Fidelity
    if "run date" in cols or (
        has("action", "symbol") and ("run date" in joined or "settlement date" in cols)
    ):
        if "run date" in cols or "acquisition date" in cols:
            return "fidelity"
    if has("action", "symbol", "quantity") and "commission ($)" in cols:
        return "fidelity"

    # Schwab
    if has("fees & comm") or ("fees & comm" in joined):
        return "schwab"
    if has("date", "action", "symbol", "quantity", "price") and "amount" in cols:
        # could be schwab or etrade — schwab often has "Fees & Comm"
        if "fees & comm" in cols or "fees and comm" in cols:
            return "schwab"

    # IBKR Flex / Activity
    if has("symbol") and (
        "comm/fee" in cols or "comm fee" in cols or "ibcommission" in cols
        or "trades" in cols and "t" in cols
        or ("proceeds" in cols and "basis" in cols)
    ):
        return "ibkr"
    if "datetime" in cols and has("symbol", "quantity") and "t" in cols:
        return "ibkr"
    if has("symbol", "quantity", "t", "price") and "proceeds" in cols:
        return "ibkr"

    # Robinhood
    if "trans code" in cols or "instrument" in cols and "trans code" in joined:
        return "robinhood"
    if has("activity date", "instrument") or has("activity date", "trans code"):
        return "robinhood"

    # E*TRADE
    if has("transaction date") and has("transaction type"):
        return "etrade"
    if "transaction type" in cols and has("symbol", "quantity"):
        return "etrade"

    # Webull
    if has("name") and has("side") and has("filled"):
        return "webull"
    if "filled qty" in cols or "avg price" in cols and "status" in cols and has("symbol"):
        return "webull"

    # Moomoo
    if has("code") and has("direction") or (
        "fill time" in cols or "order id" in cols and has("code")
    ):
        return "moomoo"
    if "fill qty" in cols or ("name" in cols and "code" in cols and "direction" in cols):
        return "moomoo"

    # TD / thinkorswim
    if "exec time" in cols or "pos effect" in cols:
        return "td_tos"
    if has("side") and has("symbol") and "net price" in cols:
        return "td_tos"

    # Vanguard
    if "trade date" in cols and "settlement date" in cols and has("symbol"):
        return "vanguard"
    if "investment name" in cols and has("transaction type"):
        return "vanguard"

    # Generic with side/action
    if has("symbol") and (has("date") or has("trade date")) and (
        has("quantity") or has("qty") or has("shares")
    ) and (has("price") or has("fill price") or has("avg price")):
        if has("side") or has("action") or has("type") or has("buy/sell"):
            return "generic_sided"
        return "generic_signed"

    return "unknown"


# ---------------------------------------------------------------------------
# per-broker parsers
# ---------------------------------------------------------------------------

def _parse_stockevents(df: pd.DataFrame) -> pd.DataFrame:
    cmap = _cols_map(df)
    sym = _find_col(cmap, "symbol", "ticker")
    date = _find_col(cmap, "date", "trade date")
    qty = _find_col(cmap, "quantity", "qty", "shares")
    price = _find_col(cmap, "price", "fill price")
    fees = _find_col(cmap, "fees amount", "fee", "fees", "commission")
    rows = []
    for _, r in df.iterrows():
        q = _qty(r.get(qty))
        p = _money(r.get(price))
        if q is None or p is None:
            continue
        rows.append(
            {
                "Symbol": _clean_symbol(r.get(sym)),
                "Date": r.get(date),
                "Quantity": q,
                "Price": abs(p),
                "Fees Amount": abs(_money(r.get(fees)) or 0.0) if fees else 0.0,
            }
        )
    return _finalize(rows, "StockEvents")


def _parse_with_side(
    df: pd.DataFrame,
    source: str,
    *,
    sym_keys: tuple[str, ...],
    date_keys: tuple[str, ...],
    qty_keys: tuple[str, ...],
    price_keys: tuple[str, ...],
    side_keys: tuple[str, ...],
    fee_keys: tuple[str, ...] = (),
    desc_keys: tuple[str, ...] = (),
    signed_qty_ok: bool = True,
) -> pd.DataFrame:
    cmap = _cols_map(df)
    sym_c = _find_col(cmap, *sym_keys)
    date_c = _find_col(cmap, *date_keys)
    qty_c = _find_col(cmap, *qty_keys)
    price_c = _find_col(cmap, *price_keys)
    side_c = _find_col(cmap, *side_keys) if side_keys else None
    fee_c = _find_col(cmap, *fee_keys) if fee_keys else None
    desc_c = _find_col(cmap, *desc_keys) if desc_keys else None

    if not all([sym_c, date_c, qty_c, price_c]):
        raise ValueError(
            f"{source}: could not map required columns. Found: {list(df.columns)}"
        )

    rows = []
    for _, r in df.iterrows():
        action = str(r.get(side_c, "")) if side_c else ""
        if side_c and not _is_trade_action(action):
            continue
        symbol = _clean_symbol(r.get(sym_c))
        desc = str(r.get(desc_c, "")) if desc_c else ""
        if not symbol or symbol.lower() in ("nan", "none"):
            # Fidelity sometimes puts symbol only in description
            continue
        if _looks_like_option(symbol, desc):
            continue

        q = _qty(r.get(qty_c))
        p = _money(r.get(price_c))
        if q is None or p is None or q == 0:
            continue
        p = abs(p)

        sign = _side_sign(action) if side_c else None
        if sign is None and signed_qty_ok and q < 0:
            signed_q = q
        elif sign is None and signed_qty_ok and q > 0 and not side_c:
            signed_q = q
        elif sign is not None:
            signed_q = abs(q) * sign
        elif signed_qty_ok:
            signed_q = q
        else:
            continue

        fees = abs(_money(r.get(fee_c)) or 0.0) if fee_c else 0.0
        rows.append(
            {
                "Symbol": symbol,
                "Date": r.get(date_c),
                "Quantity": signed_q,
                "Price": p,
                "Fees Amount": fees,
                "Description": desc,
            }
        )
    return _finalize(rows, source)


def _parse_fidelity(df: pd.DataFrame) -> pd.DataFrame:
    return _parse_with_side(
        df,
        "Fidelity",
        sym_keys=("symbol", "ticker"),
        date_keys=("run date", "date", "trade date", "settlement date"),
        qty_keys=("quantity", "qty", "shares"),
        price_keys=("price ($)", "price", "price($)"),
        side_keys=("action", "type", "transaction type"),
        fee_keys=("commission", "fees", "fees ($)", "commission ($)", "fee"),
        desc_keys=("description", "desc"),
    )


def _parse_schwab(df: pd.DataFrame) -> pd.DataFrame:
    return _parse_with_side(
        df,
        "Charles Schwab",
        sym_keys=("symbol", "ticker"),
        date_keys=("date", "trade date"),
        qty_keys=("quantity", "qty", "shares"),
        price_keys=("price", "price ($)"),
        side_keys=("action", "type"),
        fee_keys=("fees & comm", "fees and comm", "fees", "commission", "commissions"),
        desc_keys=("description",),
    )


def _parse_ibkr(df: pd.DataFrame) -> pd.DataFrame:
    cmap = _cols_map(df)
    # Activity statement "Trades" often has: Symbol, Date/Time, Quantity, T, Price, Comm/Fee
    sym = _find_col(cmap, "symbol")
    date = _find_col(cmap, "date/time", "datetime", "date time", "trade date", "date")
    qty = _find_col(cmap, "quantity", "qty")
    price = _find_col(cmap, "price", "t. price", "trade price")
    side = _find_col(cmap, "t", "side", "buy/sell", "code")
    fees = _find_col(cmap, "comm/fee", "comm fee", "commission", "ibcommission", "fees")
    asset = _find_col(cmap, "asset category", "assetcategory", "asset class")

    rows = []
    for _, r in df.iterrows():
        if asset:
            a = str(r.get(asset, "")).strip().lower()
            if a and a not in ("stocks", "stock", "equity", "equities", "stk", ""):
                if a in ("options", "option", "futures", "forex", "fop", "war"):
                    continue
        symbol = _clean_symbol(r.get(sym))
        if not symbol or _looks_like_option(symbol):
            continue
        q = _qty(r.get(qty))
        p = _money(r.get(price))
        if q is None or p is None or q == 0:
            continue
        # IB often uses signed quantity (buy +, sell −)
        if q < 0:
            signed_q = q
        else:
            sign = _side_sign(r.get(side)) if side else None
            # IB "T" column sometimes is just "BUY"/"SELL" or empty with signed qty
            if sign is None:
                # Data column "Code" may contain "O;C" etc — ignore
                side_raw = str(r.get(side, "")).strip().upper() if side else ""
                if side_raw in ("BUY", "B", "BOT"):
                    sign = 1.0
                elif side_raw in ("SELL", "S", "SLD"):
                    sign = -1.0
            signed_q = abs(q) * sign if sign is not None else q
        rows.append(
            {
                "Symbol": symbol,
                "Date": r.get(date),
                "Quantity": signed_q,
                "Price": abs(p),
                "Fees Amount": abs(_money(r.get(fees)) or 0.0) if fees else 0.0,
            }
        )
    return _finalize(rows, "Interactive Brokers")


def _parse_robinhood(df: pd.DataFrame) -> pd.DataFrame:
    return _parse_with_side(
        df,
        "Robinhood",
        sym_keys=("instrument", "symbol", "ticker"),
        date_keys=("activity date", "process date", "settle date", "date"),
        qty_keys=("quantity", "qty", "shares"),
        price_keys=("price", "average price"),
        side_keys=("trans code", "transaction type", "type", "side", "code"),
        fee_keys=("fees", "fee", "commission"),
        desc_keys=("description",),
    )


def _parse_etrade(df: pd.DataFrame) -> pd.DataFrame:
    return _parse_with_side(
        df,
        "E*TRADE",
        sym_keys=("symbol", "ticker"),
        date_keys=("transaction date", "trade date", "date"),
        qty_keys=("quantity", "qty", "shares"),
        price_keys=("price", "amount per share"),
        side_keys=("transaction type", "type", "action"),
        fee_keys=("commission", "fees", "fee"),
        desc_keys=("description",),
    )


def _parse_webull(df: pd.DataFrame) -> pd.DataFrame:
    return _parse_with_side(
        df,
        "Webull",
        sym_keys=("symbol", "name", "ticker", "code"),
        date_keys=("fill time", "time", "date", "placed time"),
        qty_keys=("filled", "filled qty", "qty", "quantity", "filled quantity"),
        price_keys=("avg price", "price", "fill price", "average price"),
        side_keys=("side", "action", "direction"),
        fee_keys=("fee", "fees", "commission"),
        desc_keys=("name", "status"),
    )


def _parse_moomoo(df: pd.DataFrame) -> pd.DataFrame:
    return _parse_with_side(
        df,
        "Moomoo",
        sym_keys=("code", "symbol", "ticker", "name"),
        date_keys=("fill time", "time", "date", "create time", "updated time"),
        qty_keys=("fill qty", "filled qty", "qty", "quantity", "dealt qty"),
        price_keys=("avg price", "fill price", "price", "dealt avg price"),
        side_keys=("direction", "side", "action", "buy/sell"),
        fee_keys=("fee", "fees", "commission", "total fee"),
        desc_keys=("name", "status"),
    )


def _parse_td_tos(df: pd.DataFrame) -> pd.DataFrame:
    return _parse_with_side(
        df,
        "TD Ameritrade / thinkorswim",
        sym_keys=("symbol", "ticker"),
        date_keys=("exec time", "date", "trade date", "time"),
        qty_keys=("qty", "quantity", "shares"),
        price_keys=("price", "net price", "avg price"),
        side_keys=("side", "pos effect", "action"),
        fee_keys=("commissions", "commission", "fees", "fee"),
        desc_keys=("description",),
    )


def _parse_vanguard(df: pd.DataFrame) -> pd.DataFrame:
    return _parse_with_side(
        df,
        "Vanguard",
        sym_keys=("symbol", "ticker", "investment name"),
        date_keys=("trade date", "date", "settlement date"),
        qty_keys=("shares", "quantity", "qty"),
        price_keys=("share price", "price"),
        side_keys=("transaction type", "type", "action"),
        fee_keys=("commission", "fees", "fee"),
        desc_keys=("investment name", "description"),
    )


def _parse_generic(df: pd.DataFrame, source: str = "Generic") -> pd.DataFrame:
    return _parse_with_side(
        df,
        source,
        sym_keys=("symbol", "ticker", "stock", "instrument", "code"),
        date_keys=("date", "trade date", "datetime", "time", "fill time", "run date"),
        qty_keys=("quantity", "qty", "shares", "filled", "fill qty"),
        price_keys=("price", "fill price", "avg price", "average price", "share price"),
        side_keys=("side", "action", "type", "buy/sell", "transaction type", "direction", "t"),
        fee_keys=("fees amount", "fees", "fee", "commission", "comm/fee", "fees & comm"),
        desc_keys=("description", "name"),
        signed_qty_ok=True,
    )


PARSERS = {
    "stockevents": _parse_stockevents,
    "fidelity": _parse_fidelity,
    "schwab": _parse_schwab,
    "ibkr": _parse_ibkr,
    "robinhood": _parse_robinhood,
    "etrade": _parse_etrade,
    "webull": _parse_webull,
    "moomoo": _parse_moomoo,
    "td_tos": _parse_td_tos,
    "vanguard": _parse_vanguard,
    "generic_sided": lambda df: _parse_generic(df, "Generic (with side)"),
    "generic_signed": lambda df: _parse_generic(df, "Generic (signed qty)"),
}


def load_broker_csv(
    path_or_buffer: CanonicalBuffer,
    *,
    broker: str | None = None,
) -> tuple[pd.DataFrame, str]:
    """Load any supported broker CSV → (canonical trades df, detected source label).

    Parameters
    ----------
    path_or_buffer :
        File path, uploaded bytes/file, or text.
    broker :
        Optional force key from PARSERS (e.g. ``"fidelity"``). Auto-detect if None.
    """
    text = _read_text(path_or_buffer)
    if not text.strip():
        raise ValueError("CSV file is empty.")

    # First pass: try reading with common header offsets
    df = _read_csv_smart(text)
    if df.empty or len(df.columns) < 2:
        # retry scanning for a header that has symbol+date
        df = _read_csv_smart(text, ("symbol", "date"))
    if df.empty:
        raise ValueError("Could not parse any rows from CSV.")

    # Drop trailing disclaimer rows (Schwab/Fidelity often append text)
    # Keep rows that have at least one non-empty cell in first 3 cols
    if len(df.columns) >= 1:
        first = df.iloc[:, 0].astype(str).str.strip()
        junk_start = first.str.lower().str.startswith(
            ("transactions total", "disclaimers", "the data", "courtesy of", "generated")
        )
        if junk_start.any():
            # cut from first junk if after some data
            idx = junk_start.idxmax() if junk_start.any() else None
            if idx is not None and junk_start.loc[idx]:
                # only cut if not the first row
                pos = df.index.get_loc(idx)
                if isinstance(pos, int) and pos > 0:
                    df = df.iloc[:pos]

    key = (broker or detect_broker(df)).lower().strip()
    if key in ("auto", "detect", ""):
        key = detect_broker(df)

    if key == "unknown":
        # last chance: generic
        try:
            out = _parse_generic(df, "Generic")
            if not out.empty:
                return out, "Generic"
        except Exception:
            pass
        raise ValueError(
            "Unrecognized CSV format. Need columns for symbol, date, quantity/shares, "
            f"and price (optional side/action). Found columns: {list(df.columns)}"
        )

    parser = PARSERS.get(key, _parse_generic)
    try:
        out = parser(df)
    except Exception as exc:
        # Fallback generic
        try:
            out = _parse_generic(df, f"Generic (fallback from {key})")
        except Exception:
            raise ValueError(f"Failed to parse as {key}: {exc}") from exc

    if out.empty:
        raise ValueError(
            f"Parsed as {key!r} but no stock trades found. "
            "Options/dividends/transfers are skipped. Check that the file has equity buys/sells."
        )

    source = str(out["Source"].iloc[0]) if "Source" in out.columns and len(out) else key
    return out, source


def supported_brokers_table() -> list[dict[str, str]]:
    return [
        {"Broker": "StockEvents", "Notes": "Symbol, Date, Quantity (+/−), Price"},
        {"Broker": "Fidelity", "Notes": "Positions/Activity export (Run Date, Action, Symbol…)"},
        {"Broker": "Charles Schwab", "Notes": "Transaction history CSV"},
        {"Broker": "Interactive Brokers", "Notes": "Activity/Flex trades (stocks)"},
        {"Broker": "Robinhood", "Notes": "Account statement CSV (Trans Code)"},
        {"Broker": "E*TRADE", "Notes": "Transaction history"},
        {"Broker": "Webull", "Notes": "Order / fill export"},
        {"Broker": "Moomoo", "Notes": "Filled orders export"},
        {"Broker": "TD Ameritrade / thinkorswim", "Notes": "Trade history"},
        {"Broker": "Vanguard", "Notes": "Transaction history"},
        {"Broker": "Generic", "Notes": "Any CSV with Symbol + Date + Qty + Price [+ Side]"},
    ]
