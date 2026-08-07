"""Multi-broker transaction CSV import → canonical trade schema.

Canonical output columns:
  Symbol, Date, Quantity, Price, Fees Amount, Source
  Quantity: +buy / −sell (shares)

Supported (auto-detected by headers):
  Fidelity, Charles Schwab, Interactive Brokers (Activity/Flex-ish),
  Robinhood, E*TRADE (transaction history + Orders export for live or paper),
  Webull, Moomoo, TD Ameritrade / thinkorswim, Vanguard,
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


# Tokens that commonly appear in brokerage / portfolio CSV *header* rows.
_HEADER_FIELD_TOKENS: frozenset[str] = frozenset(
    {
        "symbol",
        "ticker",
        "date",
        "time",
        "quantity",
        "qty",
        "shares",
        "price",
        "action",
        "side",
        "status",
        "fill",
        "description",
        "commission",
        "fees",
        "fee",
        "amount",
        "account",
        "market",
        "type",
        "instrument",
        "direction",
        "code",
        "name",
        "trade date",
        "run date",
        "settlement date",
        "activity date",
        "process date",
        "trans code",
        "transaction type",
        "transaction date",
        "buy/sell",
        "avg price",
        "fill price",
        "fill qty",
        "filled",
        "proceeds",
        "basis",
        "currency",
        "price currency",
        "fees amount",
        "fees percentage",
        "fees & comm",
        "comm/fee",
        "net amount",
        "exec time",
        "pos effect",
        "share price",
        "investment name",
        "order id",
        "datetime",
        "date/time",
    }
)

# Phrases typical of title / banner / metadata lines (not headers).
_TITLE_LINE_HINTS = re.compile(
    r"("
    r"as of\b|exported\b|generated\b|downloaded\b|report\b|statement\b|"
    r"account\s+\d+|paper trade|orders,\s*as of|transaction history|"
    r"brokerage|portfolio summary|for the period|date range|copyright|"
    r"confidential|page\s+\d+|prepared for"
    r")",
    re.I,
)


def _split_csv_fields(line: str) -> list[str]:
    """Split one CSV line into fields (handles quoted commas)."""
    import csv as _csv

    try:
        row = next(_csv.reader([line]))
        return [str(c).replace("\ufeff", "").strip() for c in row]
    except Exception:
        return [p.strip().strip('"') for p in line.split(",")]


def _find_header_line(text: str, must_have: tuple[str, ...] | None = None) -> int:
    """Return 0-based index of the best header line within the first ~50 rows.

    If ``must_have`` is set, require those substrings (legacy). Otherwise score
    every early line and pick the strongest header candidate so title banners
    (E*TRADE, Fidelity, Schwab, etc.) are skipped automatically.
    """
    lines = text.splitlines()
    if not lines:
        return 0

    if must_have:
        for i, line in enumerate(lines[:50]):
            low = line.lower()
            if all(m.lower() in low for m in must_have):
                return i
        # fall through to scored detection

    best_i, best_score = 0, -10_000
    for i, line in enumerate(lines[:50]):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        score = _score_header_candidate(raw)
        # Slight preference for earlier rows when scores tie
        score -= i * 0.01
        if score > best_score:
            best_score = score
            best_i = i

    # Need a minimum score so we don't treat a prose line as a header
    if best_score < 3:
        return 0
    return best_i


def _score_header_candidate(line: str) -> float:
    """Higher score ⇒ more likely a real column-header row."""
    fields = [f for f in _split_csv_fields(line) if f != ""]
    n = len(fields)
    if n == 0:
        return -100.0

    joined = " ".join(fields)
    norms = [_norm_header(f) for f in fields]
    norm_set = set(norms)

    score = 0.0

    # Column count: real headers usually have several short field names
    if n == 1:
        score -= 25.0
    elif n == 2:
        score += 1.0
    else:
        score += min(n, 12) * 0.75

    # Known header tokens (exact field match preferred)
    token_hits = 0
    for tok in _HEADER_FIELD_TOKENS:
        if tok in norm_set:
            token_hits += 1
            score += 3.0
        elif len(tok) >= 4 and any(tok == nf or tok in nf.split() for nf in norms):
            token_hits += 1
            score += 2.0

    # Strong combos used by most broker exports
    has_symbol = bool(norm_set & {"symbol", "ticker", "instrument", "code"})
    has_date = any(
        "date" in nf or nf in {"time", "datetime", "date/time", "exec time", "fill time"}
        for nf in norms
    )
    has_qty = bool(norm_set & {"quantity", "qty", "shares", "fill", "filled", "fill qty"})
    has_price = any("price" in nf for nf in norms) or "fill" in norm_set
    if has_symbol:
        score += 4.0
    if has_date:
        score += 3.0
    if has_qty:
        score += 3.0
    if has_price:
        score += 2.0
    if has_symbol and (has_date or has_qty):
        score += 4.0

    # Header cells are usually short labels, not sentences
    avg_len = sum(len(f) for f in fields) / max(n, 1)
    if avg_len <= 18:
        score += 2.0
    elif avg_len > 40:
        score -= 8.0

    # Title / banner language without enough header tokens
    if _TITLE_LINE_HINTS.search(joined) and token_hits < 3:
        score -= 30.0

    # Entire line is one long quoted title
    if n <= 2 and len(joined) > 50 and token_hits < 2:
        score -= 20.0

    # Data-row penalty: many purely numeric fields → likely not a header
    numeric_like = 0
    for f in fields:
        if re.fullmatch(r"[\d,.$+\-()]+", f.replace(" ", "")):
            numeric_like += 1
    if n >= 3 and numeric_like / n >= 0.5:
        score -= 15.0

    return score


def detect_title_skip_rows(text: str) -> int:
    """Public helper: how many leading rows to skip before the CSV header."""
    return _find_header_line(text)


def _read_csv_smart(text: str, header_hints: tuple[str, ...] | None = None) -> pd.DataFrame:
    """Read CSV, auto-skipping title/banner rows above the real header."""
    skip = _find_header_line(text, header_hints)
    try:
        df = pd.read_csv(io.StringIO(text), skiprows=skip, dtype=str, keep_default_na=False)
    except Exception:
        try:
            df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
        except Exception:
            return pd.DataFrame()
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

    # Simple signed-qty export (Symbol/Date/Quantity/Price + optional fee columns)
    if has("symbol", "quantity", "price") and has("date") and (
        "fees amount" in cols or "fees percentage" in cols or "price currency" in cols
    ):
        return "simple_signed"

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

    # E*TRADE Orders export (live account or paper trade)
    # Columns: Symbol, Status, Fill, Description, Market, Time, Account
    # Title line examples:
    #   "Account 459239347 Orders, as of …"
    #   "Paper Trade Account Orders, as of …"
    if has("symbol", "status", "fill", "description") and (
        "account" in cols or "market" in cols or "time" in cols
    ):
        return "etrade_orders"
    if has("status", "fill") and has("description") and has("symbol"):
        return "etrade_orders"

    # E*TRADE transaction history
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

def _parse_simple_signed(df: pd.DataFrame) -> pd.DataFrame:
    """Symbol / Date / signed Quantity / Price (optional fees)."""
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
    return _finalize(rows, "Generic (signed qty)")


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


_FILL_RE = re.compile(
    r"^\s*([\d,]+(?:\.\d+)?)\s*@\s*\$?\s*([\d,]+(?:\.\d+)?)\s*$",
    re.I,
)
_DESC_SIDE_RE = re.compile(r"^\s*(buy|sell)\b", re.I)
_DESC_OPTION_RE = re.compile(
    r"\b("
    r"call|put|calls|puts|vertical|iron\s*condor|condor|butterfly|"
    r"straddle|strangle|calendar|diagonal|credit\s*spread|debit\s*spread|"
    r"covered\s*call|cash\s*secured|option"
    r")\b",
    re.I,
)
_DESC_STOCK_QTY_RE = re.compile(
    r"^\s*(?:buy|sell)\s+(\d+(?:\.\d+)?)\s+(?:shares?\s+(?:of\s+)?)?",
    re.I,
)


def _parse_fill_qty_price(fill_val) -> tuple[float | None, float | None]:
    """Parse E*TRADE paper Fill column: ``1 @ 5.40`` or ``2 @ 0.19``."""
    if fill_val is None or (isinstance(fill_val, float) and pd.isna(fill_val)):
        return None, None
    s = str(fill_val).strip()
    if s in ("", "--", "-", "n/a", "N/A"):
        return None, None
    m = _FILL_RE.match(s)
    if not m:
        return None, None
    q = _qty(m.group(1))
    p = _money(m.group(2))
    return q, p


def _is_option_order_description(desc: str) -> bool:
    d = (desc or "").strip()
    if not d:
        return False
    if _DESC_OPTION_RE.search(d):
        return True
    # Strike / expiration style: "Sep-18-26 7000/6800" or "Jul-31-26 44"
    if re.search(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\d{1,2}-\d{2}\b", d, re.I):
        if re.search(r"\d{2,5}(?:\.\d+)?(?:/\d{2,5}(?:\.\d+)?)?", d):
            return True
    return False


def _parse_etrade_orders(df: pd.DataFrame) -> pd.DataFrame:
    """E*TRADE **Orders** export (live brokerage or paper trade).

    Same CSV shape for both. Title line examples::

        Account 459239347 Orders, as of 08/06/26 at 09:37 PM EST
        Paper Trade Account Orders, as of 08/06/26 at 09:33 PM EST

    Columns (after the title line)::

        Symbol, Status, Fill, Description, Market, Time, Account

    Rules:
      - Only **Filled** rows
      - Fill text ``qty @ price`` supplies quantity and fill price
      - Description supplies Buy/Sell (e.g. ``Buy 269 Shares @ 66 Limit…``)
      - Equity stock fills only (option verticals/calls/puts are skipped)
    """
    cmap = _cols_map(df)
    sym_c = _find_col(cmap, "symbol", "ticker")
    status_c = _find_col(cmap, "status")
    fill_c = _find_col(cmap, "fill", "filled")
    desc_c = _find_col(cmap, "description", "desc")
    time_c = _find_col(cmap, "time", "date", "order time", "fill time")

    if not all([sym_c, fill_c, desc_c, time_c]):
        raise ValueError(
            f"E*TRADE: missing columns. Found: {list(df.columns)}"
        )

    rows: list[dict] = []
    skipped_options = 0
    skipped_unfilled = 0

    for _, r in df.iterrows():
        status = str(r.get(status_c, "") if status_c else "").strip().lower()
        if status and status not in ("filled", "executed", "complete", "completed"):
            skipped_unfilled += 1
            continue

        desc = str(r.get(desc_c, "") or "")
        if _is_option_order_description(desc):
            skipped_options += 1
            continue

        q, p = _parse_fill_qty_price(r.get(fill_c))
        if q is None or p is None or q == 0:
            # Fall back to description: "Buy 100 AAPL @ 150 Limit"
            m_side = _DESC_SIDE_RE.match(desc)
            m_qty = _DESC_STOCK_QTY_RE.match(desc)
            m_px = re.search(r"@\s*\$?\s*([\d,]+(?:\.\d+)?)", desc)
            if m_qty and m_px:
                q = _qty(m_qty.group(1))
                p = _money(m_px.group(1))
            if q is None or p is None or q == 0:
                skipped_unfilled += 1
                continue

        sign = _side_sign(desc)
        if sign is None:
            m_side = _DESC_SIDE_RE.match(desc)
            if m_side:
                sign = 1.0 if m_side.group(1).lower() == "buy" else -1.0
        if sign is None:
            continue

        symbol = _clean_symbol(r.get(sym_c))
        if not symbol or _looks_like_option(symbol, desc):
            skipped_options += 1
            continue

        rows.append(
            {
                "Symbol": symbol,
                "Date": r.get(time_c),
                "Quantity": abs(q) * sign,
                "Price": abs(p),
                "Fees Amount": 0.0,
                "Description": desc,
            }
        )

    out = _finalize(rows, "E*TRADE")
    if out.empty and (skipped_options or skipped_unfilled):
        raise ValueError(
            "E*TRADE: no filled **stock** trades found. "
            f"Skipped {skipped_options} option order(s) and "
            f"{skipped_unfilled} unfilled/canceled/unparsed row(s). "
            "Only Status=Filled equity stock orders are imported "
            "(options and Open/Canceled/Expired rows are ignored)."
        )
    return out


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
    "simple_signed": _parse_simple_signed,
    "fidelity": _parse_fidelity,
    "schwab": _parse_schwab,
    "ibkr": _parse_ibkr,
    "robinhood": _parse_robinhood,
    "etrade": _parse_etrade,
    "etrade_orders": _parse_etrade_orders,
    "etrade_paper": _parse_etrade_orders,  # alias (older name)
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

    # Auto-detect header row for *all* uploads (skips title/banner/metadata lines).
    # Works for E*TRADE Orders, Fidelity, Schwab, generic signed-qty, etc.
    df = _read_csv_smart(text)
    if df.empty or len(df.columns) < 2:
        # Fallbacks with stronger hints if scoring was ambiguous
        for hints in (
            ("symbol", "date"),
            ("symbol", "status"),
            ("ticker", "date"),
            ("instrument", "quantity"),
        ):
            df = _read_csv_smart(text, hints)
            if not df.empty and len(df.columns) >= 2:
                break
    if df.empty or len(df.columns) < 2:
        raise ValueError(
            "Could not find a CSV header row. "
            "Expected columns like Symbol, Date, Quantity, Price "
            "(or broker equivalents). Check that the file is a trade export."
        )

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
    except ValueError:
        # Intentional parse outcome (e.g. options-only Orders file) — do not
        # fall back to generic, which can mis-read Fill/Description columns.
        raise
    except Exception as exc:
        # Unexpected failure — try generic only for loosely-related formats
        if key in ("etrade_orders", "etrade_paper", "simple_signed"):
            raise ValueError(f"Failed to parse as {key}: {exc}") from exc
        try:
            out = _parse_generic(df, f"Generic (fallback from {key})")
        except Exception:
            raise ValueError(f"Failed to parse as {key}: {exc}") from exc

    if out.empty:
        if key in ("etrade_orders", "etrade_paper"):
            raise ValueError(
                "E*TRADE: no filled stock trades found. "
                "Only Status=Filled equity stock orders are imported; "
                "options and Open/Canceled/Expired rows are skipped."
            )
        raise ValueError(
            f"Parsed as {key!r} but no stock trades found. "
            "Options/dividends/transfers are skipped. Check that the file has equity buys/sells."
        )

    source = str(out["Source"].iloc[0]) if "Source" in out.columns and len(out) else key
    return out, source


def supported_brokers_table() -> list[dict[str, str]]:
    return [
        {"Broker": "Fidelity", "Notes": "Positions/Activity export (Run Date, Action, Symbol…)"},
        {"Broker": "Charles Schwab", "Notes": "Transaction history CSV"},
        {"Broker": "Interactive Brokers", "Notes": "Activity/Flex trades (stocks)"},
        {"Broker": "Robinhood", "Notes": "Account statement CSV (Trans Code)"},
        {
            "Broker": "E*TRADE",
            "Notes": "Transaction history or Orders export (live/paper); filled stock orders only",
        },
        {"Broker": "Webull", "Notes": "Order / fill export"},
        {"Broker": "Moomoo", "Notes": "Filled orders export"},
        {"Broker": "TD Ameritrade / thinkorswim", "Notes": "Trade history"},
        {"Broker": "Vanguard", "Notes": "Transaction history"},
        {
            "Broker": "Generic",
            "Notes": "Symbol + Date + Qty + Price [+ Side]; signed qty (+buy / −sell) also OK",
        },
    ]
