# Futu OpenAPI market data

Quantarium reads Futu (moomoo) daily bars through a small bridge that runs in its own Python environment, like the backtrader cross-check. Quantarium never imports Futu's package and gains no dependency; the bridge writes a price file and Quantarium reads it. The bridge fetches **market data only**: it has no trading code, and Quantarium places no orders (`AGENTS.md`, section 1).

This repository distributes the bridge's compatibility code, not Futu market data. Each user obtains data through their own account for their own local use and checks the provider's terms. Keep fetched prices and datasets derived from them out of Git and the public repository; `/data/` and `/scenarios/` are ignored for local work.

Automated bridge tests use fake candles and no Futu account. The maintainer checks the first real run by hand.

## OpenD settings

- Set the API listening address to `127.0.0.1`, not `0.0.0.0` or a network interface address. The bridge accepts `localhost` as input but connects to the literal `127.0.0.1` address. The API port must be between 1 and 65535.
- Leave Telnet disabled. Visual OpenD needs WebSocket for its own GUI, so keep its WebSocket listener on `127.0.0.1` only. If using command-line OpenD without the GUI, disable WebSocket too. See [Futu's Visual OpenD settings](https://openapi.futunn.com/futu-api-doc/en/quick/opend-base.html).
- Never set or enter a trade-unlock password for this market-data bridge. Prefer the GUI for login rather than putting a login password in `FutuOpenD.xml`.
- Never copy OpenD's configuration or logs from `%APPDATA%\com.futunn.FutuOpenD` into the repository. The Python SDK also writes `py_*.log` files in `%APPDATA%\com.futunn.FutuOpenD\Log`; keep those local, including when troubleshooting.

## Setup

1. Install and start **Futu OpenD** from Futu's site, and sign in to it. OpenD holds your login; the bridge needs no keys or passwords.
2. Create the bridge's environment **outside** the Quantarium repository, for example `C:\Projects\quantarium-futu\.venv`, and install Futu's package into it, pinned to an exact release at least seven days old. Check what it pulls in first:

   ```bash
   py -m venv C:/Projects/quantarium-futu/.venv
   ```

   ```bash
   C:/Projects/quantarium-futu/.venv/Scripts/python.exe -I -m pip --isolated install --dry-run --report report.json --index-url https://pypi.org/simple futu-api==VERSION
   ```

   Look at every package and its upload date in `report.json`, then install the audited versions and hashes in this environment only. `futu-api` brings pandas, protobuf, and other packages; its source archive also needs pinned build dependencies.

## Fetching bars

```bash
C:/Projects/quantarium-futu/.venv/Scripts/python.exe -I tools/futu_bridge/bridge.py --codes US.AAPL,US.MSFT --from 2024-01-02 --through 2024-06-28 --output data/futu_us.csv
```

- `--codes` takes Futu codes; the symbol is the part after the market (`US.AAPL` becomes `AAPL`). Map a code to another symbol with `HK.00700=TENCENT`, since Quantarium symbols use letters, digits, `.`, and `-`.
- `--adjust forward` (the default) asks for split- and dividend-adjusted prices, like download sites, so no corporate actions are needed. `--adjust none` gives raw prices.
- `--append` is allowed only with `--adjust none` (raw prices). It adds sessions after each symbol's last date, keeping earlier rows as first seen; run that combination each evening to advance a `csv` Feed experiment ([README](../README.md#real-data-without-an-api-key)). Forward-adjusted history changes after corporate actions, so re-fetch the whole range to a new file instead of appending to an adjusted file.
- `--host` must be a loopback address (default `127.0.0.1`); `--port` is OpenD's (default `11111`).
- Inside the Quantarium checkout, `--output` must resolve under `data/`, `scenarios/`, or `private/`. Paths outside the checkout are allowed. Keep all fetched data local and out of Git.

The output is a Static price file: use it as a Static `data_path`, cut scenarios from it with `scenario slice`, or follow it with a `csv` Feed. The `csv` Feed checks sessions against the NYSE calendar, so use it for US codes; bars from other markets work in Static runs and scenarios.

Futu limits how much candle history an account may request in a period, depending on the account; its OpenAPI documentation gives the current quota. For raw prices, fetch a long history once and then append with `--adjust none`. For forward-adjusted prices, re-fetch the full range after a corporate action.

## Alpaca

Alpaca's daily bars (Feed Batch) and minute bars (Live Feed) are built in; see the README's Alpaca sections. Keys stay in `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY`.
