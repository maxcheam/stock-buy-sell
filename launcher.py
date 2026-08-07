"""
Desktop launcher for the Stock Buy/Sell Ladder Streamlit app.

Works in two modes:
  • Dev:   python -m stock_buy_sell.launcher
  • Frozen: StockBuySellLadder.exe  (PyInstaller bundle)

Opens the default browser to the local Streamlit server.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path


def app_root() -> Path:
    """Directory that contains the executable (or project root in dev)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_root() -> Path:
    """PyInstaller extract dir (_MEIPASS) or project root in dev."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def resolve_app_script() -> Path:
    """Locate stock_buy_sell/app.py inside the bundle or source tree."""
    candidates = [
        bundle_root() / "stock_buy_sell" / "app.py",
        app_root() / "stock_buy_sell" / "app.py",
        Path(__file__).resolve().parent / "app.py",
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError(
        "Could not find stock_buy_sell/app.py. Looked in:\n"
        + "\n".join(f"  - {c}" for c in candidates)
    )


def ensure_paths() -> Path:
    """Put project / bundle on sys.path so `import stock_buy_sell` works."""
    root = bundle_root()
    for p in (str(root), str(app_root())):
        if p not in sys.path:
            sys.path.insert(0, p)
    # User-writable side files (uploads not needed; sample CSV lives next to exe)
    os.environ.setdefault("STOCK_BUY_SELL_USER_DIR", str(app_root()))
    return resolve_app_script()


def main() -> int:
    try:
        app_script = ensure_paths()
    except Exception as exc:
        _fail(f"Startup path error: {exc}")
        return 1

    # Streamlit / Tornado quieter defaults for a desktop-style launch
    os.environ.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    os.environ.setdefault("STREAMLIT_SERVER_HEADLESS", "true")
    os.environ.setdefault("STREAMLIT_GLOBAL_DEVELOPMENT_MODE", "false")

    port = os.environ.get("STOCK_BUY_SELL_PORT", "8505")
    address = os.environ.get("STOCK_BUY_SELL_ADDRESS", "127.0.0.1")

    # Build argv for streamlit CLI
    sys.argv = [
        "streamlit",
        "run",
        str(app_script),
        f"--server.port={port}",
        f"--server.address={address}",
        "--server.headless=true",
        "--browser.gatherUsageStats=false",
        "--global.developmentMode=false",
        "--server.fileWatcherType=none",
    ]

    print("=" * 56)
    print("  Stock Buy/Sell Ladder")
    print(f"  Opening http://{address}:{port}")
    print("  Leave this window open while using the app.")
    print("  Press Ctrl+C here to quit.")
    print("=" * 56)

    try:
        from streamlit.web import cli as stcli

        return int(stcli.main() or 0)
    except SystemExit as e:
        code = e.code
        return int(code) if isinstance(code, int) else (0 if code is None else 1)
    except Exception as exc:
        _fail(f"Failed to start Streamlit: {exc}\n{traceback.format_exc()}")
        return 1


def _fail(message: str) -> None:
    print(message, file=sys.stderr)
    # Keep the console open when double-clicked as an .exe
    if getattr(sys, "frozen", False):
        try:
            input("\nPress Enter to close…")
        except EOFError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
