# Trade Monitor

Binance USDT-M perpetual futures market monitor with a small dashboard, scanner, signal watcher, and paper-position allocator.

This project is for monitoring and paper trading by default. It reads public Binance Futures market data and writes local JSON state files. Real Binance USD-M Futures orders are only sent when live trading is explicitly enabled.

## Features

- Scans Binance USDT-M perpetual futures contracts for 24h gainers.
- Ranks candidates by recent 15-minute quote-volume expansion.
- Tracks setup candidates in `watchlist.json`.
- Calculates support and resistance from recent candle structure.
- Opens paper positions when price breaks resistance with a short-window quote-volume spike.
- Tracks paper positions and estimated fees/PnL in `positions.json`.
- Records paper order history in `trade_history.json`.
- Serves a local dashboard with watchlist signals, open positions, and candlestick levels.
- Can optionally route entries/exits to Binance USD-M Futures when `TRADE_MODE=live` is explicitly enabled.

## Strategy Summary

Scanner defaults:

- 24h change between `5%` and `30%`
- no score filter
- top `30` perpetual futures gainers ranked by 15-minute quote-volume expansion

Signal defaults:

- structure timeframe: `15m`
- volume timeframe: `1m`
- spike window: `3` minutes
- minimum 15-minute quote-volume baseline: `50000` USDT
- volume multiplier: `2x`

Support and resistance are calculated from the latest 15 completed structure candles. The code first looks for confirmed swing highs/lows using two candles on each side. If no confirmed pivot exists, it falls back to the high/low of the confirmed window while excluding the newest unconfirmed edge.

An `OPEN` signal requires both:

- latest volume candle close above resistance plus `BREAKOUT_BUFFER_PCT`
- recent average quote volume greater than the higher of the minimum average quote-volume threshold or `VOL_MULT` times the prior 20-candle average quote volume

## Strategy Sizing

Each paper position targets:

```text
current equity = EQUITY + realized PnL + unrealized PnL
notional = current equity / slots
```

Before any PnL, the defaults are `10000 / 10 = 1000 USDT` notional per position. As paper PnL changes, new positions resize from current account equity.

The strategy tracks local margin usage as:

```text
margin = notional / leverage
used cash = margin + entry fee
```

New positions start with `LEVERAGE=1`. If available paper cash is not enough, the strategy doubles leverage step-by-step (`1x -> 2x -> 4x -> 8x`) until the required margin fits or `MAX_LEVERAGE` is reached. If even `MAX_LEVERAGE` is not enough, the signal is skipped.

## Live Trading

Default mode is always paper:

```text
TRADE_MODE=paper
```

Live trading requires all of the following:

```text
TRADE_MODE=live
LIVE_TRADING_CONFIRM=YES
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
```

Optional live-trading guardrails:

| Variable | Description |
| --- | --- |
| `MAX_LIVE_NOTIONAL` | Reject any live entry above this USDT notional |
| `LIVE_SYMBOLS` | Comma-separated symbol allowlist, for example `BTCUSDT,ETHUSDT` |
| `BINANCE_FUTURES_API_BASE` | API base, defaults to `https://fapi.binance.com` |

Live entries use Binance USD-M Futures market `BUY` orders. Live exits use reduce-only market `SELL` orders so stop-loss, structure exits, and take-profit exits do not open a reverse short. Quantity is rounded down to Binance symbol filters before submission.

Keep API keys out of git. Use keys with no withdrawal permission, add IP restrictions where possible, and start with small `MAX_LIVE_NOTIONAL` while validating behavior.

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
| `EQUITY` | `10000` | Paper account equity used for sizing |
| `SLOTS` | `10` | Maximum number of paper positions and sizing divisor |
| `FEE_BPS` | `10` | Fee estimate in basis points |
| `LEVEL_KLINE` | `15m` | Strategy support/resistance candle interval |
| `VOLUME_KLINE` | `1m` | Volume spike candle interval |
| `MIN_QVOL` | `50000` | 15-minute quote-volume floor used by the spike threshold |
| `VOL_MULT` | `2` | Recent volume multiplier versus prior average |
| `SPIKE_MINUTES` | `3` | Recent volume window size |
| `BREAKOUT_BUFFER_PCT` | `0.2` | Required close above resistance before treating it as a breakout |
| `SETUP_ONLY` | `1` | Only process rows marked as setup |
| `STRATEGY_SECONDS` | `1` | Strategy loop interval |
| `WATCH_SECONDS` | `5` | Watcher loop interval |
| `POSITION_TIMEOUT_SECONDS` | `3600` | Close positions after this many seconds; default is 1 hour, `0` disables |
| `LEVERAGE` | `1` | Starting leverage for new paper positions |
| `MAX_LEVERAGE` | `8` | Maximum auto-escalated leverage when paper cash is insufficient |
| `TRADE_MODE` | `paper` | `paper` keeps local simulated orders; `live` sends Binance Futures orders |

Scanner variables:

| Variable | Default | Description |
| --- | --- | --- |
| `SCAN_LIMIT` | `30` | Number of ranked gainers kept in the watchlist |
| `MIN_CHANGE` | `5` | Minimum 24h percent change |
| `MAX_CHANGE` | `30` | Maximum 24h percent change |
| `MIN_DELIVERY_DAYS` | `7` | Exclude contracts whose `deliveryDate` is within this many days |
| `SCAN_VOLUME_KLINE` | `1m` | Candle interval used to compare recent and prior quote volume |
| `SCAN_VOLUME_MINUTES` | `15` | Number of minutes in each quote-volume comparison window |
| `SCAN_SECONDS` | `10` | Scanner refresh interval |

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
- `trade_history.json`
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
It also excludes contracts whose `deliveryDate` is too close. The default exclusion window is `7` days and can be changed with `MIN_DELIVERY_DAYS`.
