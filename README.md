# Trade Monitor

Binance USDT-M perpetual futures market monitor with a small dashboard, scanner, signal watcher, and paper-position allocator.

This project is for monitoring and paper trading only. It reads public Binance Futures market data and writes local JSON state files. It does not place real exchange orders.

## Features

- Scans Binance USDT-M perpetual futures contracts for 24h gainers.
- Scores candidates by 24h move, liquidity, trade count, and green daily candle.
- Tracks setup candidates in `watchlist.json`.
- Calculates support and resistance from recent candle structure.
- Opens paper positions when price breaks resistance with a short-window quote-volume spike.
- Tracks paper positions and estimated fees/PnL in `positions.json`.
- Serves a local dashboard with watchlist signals, open positions, and candlestick levels.

## Strategy Summary

Scanner defaults:

- 24h change between `10%` and `30%`
- minimum score `70`
- top `30` perpetual futures gainers by quote volume

Signal defaults:

- structure timeframe: `15m`
- volume timeframe: `1m`
- spike window: `3` minutes
- minimum 15-minute quote-volume baseline: `50000` USDT
- volume multiplier: `2x`

Support and resistance are calculated from the latest 15 completed structure candles. The code first looks for confirmed swing highs/lows using two candles on each side. If no confirmed pivot exists, it falls back to the high/low of the confirmed window while excluding the newest unconfirmed edge.

An `OPEN` signal requires both:

- latest volume candle close above resistance
- recent quote volume greater than the higher of the minimum quote-volume threshold or `VOL_MULT` times the expected recent volume

## Dashboard

The dashboard runs on port `5050`.

The chart currently displays `5m` candles:

- `/api/klines?interval=5m&limit=96`
- about 8 hours of candles
- chart support/resistance is recalculated from that 5-minute data for display

The strategy signal timeframe can remain different from the chart timeframe. By default the strategy uses `15m` structure levels unless `LEVEL_KLINE` is changed.

## Quick Start

Run all services with Docker Compose:

```bash
docker compose up --build
```

Open the dashboard:

```text
http://localhost:5050
```

Stop services:

```bash
docker compose down
```

## Services

- `scanner`: refreshes `watchlist.json` from Binance USDT-M perpetual futures 24h ticker data
- `watcher`: prints current setup/exit signals and removes broken setups
- `strategy`: converts `OPEN` signals into paper positions
- `dashboard`: serves the web UI and JSON APIs

## Environment Variables

Common variables:

| Variable | Default | Description |
| --- | --- | --- |
| `EQUITY` | `1000` | Paper account equity used for sizing |
| `FEE_BPS` | `10` | Fee estimate in basis points |
| `LEVEL_KLINE` | `15m` | Strategy support/resistance candle interval |
| `VOLUME_KLINE` | `1m` | Volume spike candle interval |
| `MIN_QVOL` | `50000` | 15-minute quote-volume floor used by the spike threshold |
| `VOL_MULT` | `2` | Recent volume multiplier versus prior average |
| `SPIKE_MINUTES` | `3` | Recent volume window size |
| `MAX_SYMBOLS` | `50` | Maximum watchlist rows to evaluate |
| `SETUP_ONLY` | `1` | Only process rows marked as setup |

Scanner variables:

| Variable | Default | Description |
| --- | --- | --- |
| `SCAN_THRESHOLD` | `70` | Minimum scanner score |
| `SCAN_LIMIT` | `30` | Number of top gainers considered |
| `MIN_CHANGE` | `10` | Minimum 24h percent change |
| `MAX_CHANGE` | `30` | Maximum 24h percent change |
| `SCAN_SECONDS` | `600` | Scanner refresh interval |

## Local Commands

Run one scanner pass:

```bash
python3 scanner.py --once
```

Print current signals once:

```bash
python3 watcher.py --once
```

Run self-checks:

```bash
python3 scanner.py --demo
python3 watcher.py --demo
python3 strategy.py --demo
python3 -m py_compile scanner.py watcher.py strategy.py dashboard.py
```

## State Files

These files are generated locally and intentionally ignored by git:

- `watchlist.json`
- `positions.json`
- `pending.json`
- `*.log`
- `*.pid`
- `charts/`

They contain runtime state, generated output, or machine-local process data.

## Binance Data Source

The runtime data source is Binance USDT-M Futures:

- base URL: `https://fapi.binance.com`
- exchange info: `/fapi/v1/exchangeInfo`
- 24h tickers: `/fapi/v1/ticker/24hr`
- candles: `/fapi/v1/klines`
- mark reference used by the dashboard: `/fapi/v1/ticker/price`

The scanner filters for `contractType = PERPETUAL`, `status = TRADING`, and `quoteAsset = USDT`.
