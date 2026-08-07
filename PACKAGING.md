# Packaging as a shareable Windows app

Yes — this Streamlit app can be turned into a **portable Windows folder** with an `.exe` that others can run **without installing Python**.

## What you share

After building, zip the entire folder:

```
dist/StockBuySellLadder/
  StockBuySellLadder.exe   ← double-click this
  _internal/               ← required libraries (do not delete)
  input/                   ← optional sample CSV
  README.txt
```

Recipients unzip and run `StockBuySellLadder.exe`. A console window stays open (that is the server); the browser opens automatically.

## Build (on your Windows machine)

```bat
build_exe.bat
```

Or manually:

```bat
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean stock_buy_sell.spec
```

Build time is often 3–10 minutes. Output size is typically **~200–500 MB** (Streamlit + pandas + plotly).

## Requirements for recipients

| Need | Why |
|------|-----|
| Windows 10/11 64-bit | Native build target |
| Internet | Live prices (yfinance / Yahoo) |
| Whole folder | `.exe` alone will not work; `_internal` is required |

## Limitations (honest)

1. **Not a tiny single file** — Streamlit bundles are large; we use **onedir** (folder) because it is far more reliable than one giant `.exe`.
2. **Antivirus** may flag PyInstaller apps the first time (unsigned binary). Signing a certificate removes most of that friction.
3. **Still needs the network** for market prices.
4. **Cross-platform** — this build is for Windows. macOS/Linux need a rebuild on those OSes.
5. Not a “real” native desktop UI — it still runs a local web server under the hood.

## Alternatives if an .exe is awkward

| Option | Best for |
|--------|----------|
| **Streamlit Community Cloud** | Free web link; no install for friends |
| **Zip of source + `run_buy_sell.bat`** | Recipients who have Python |
| **Docker image** | Technical users |

## Dev launcher (no build)

```bat
python -m stock_buy_sell.launcher
```
