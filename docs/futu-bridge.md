# Futu OpenAPI market data

Quantarium reads Futu (moomoo) daily bars through a small bridge that runs in its own Python environment, like the backtrader cross-check. Quantarium never imports Futu's package and gains no dependency; the bridge writes a price file and Quantarium reads it. The bridge fetches **market data only**: it has no trading code, and Quantarium places no orders (`AGENTS.md`, section 1).

Quantarium's developers do not have Futu accounts, so the bridge is tested with fake candles. The first real run should be checked by hand.

## Setup

1. Install and start **Futu OpenD** from Futu's site, and sign in to it. OpenD holds your login; the bridge needs no keys or passwords.
2. Create the bridge's environment **outside** the Quantarium repository, for example `C:\Projects\quantarium-futu\.venv`, and install Futu's package into it, pinned to an exact release at least seven days old. Check what it pulls in first:

   ```bash
   py -m venv C:/Projects/quantarium-futu/.venv
   ```

   ```bash
   C:/Projects/quantarium-futu/.venv/Scripts/python.exe -m pip install --dry-run --report report.json futu-api==VERSION
   ```

   Look at every package and its upload date in `report.json`, then install the same pinned version without `--dry-run`. `futu-api` brings pandas, protobuf, and other packages; they stay in this environment only.

## Fetching bars

```bash
C:/Projects/quantarium-futu/.venv/Scripts/python.exe -I tools/futu_bridge/bridge.py --codes US.AAPL,US.MSFT --from 2024-01-02 --through 2024-06-28 --output data/futu_us.csv
```

- `--codes` takes Futu codes; the symbol is the part after the market (`US.AAPL` becomes `AAPL`). Map a code to another symbol with `HK.00700=TENCENT`, since Quantarium symbols use letters, digits, `.`, and `-`.
- `--adjust forward` (the default) asks for split- and dividend-adjusted prices, like download sites, so no corporate actions are needed. `--adjust none` gives raw prices.
- `--append` adds only sessions after each symbol's last date, keeping earlier rows as first seen. Run it each evening to advance a `csv` Feed experiment ([README](../README.md#real-data-without-an-api-key)).
- `--host` must be a loopback address (default `127.0.0.1`); `--port` is OpenD's (default `11111`).

The output is a Static price file: use it as a Static `data_path`, cut scenarios from it with `scenario slice`, or follow it with a `csv` Feed. The `csv` Feed checks sessions against the NYSE calendar, so use it for US codes; bars from other markets work in Static runs and scenarios.

Futu limits how much candle history an account may request in a period, depending on the account; its OpenAPI documentation gives the current quota. Fetch a long history once into a file, then append.

## Alpaca

Alpaca's daily bars (Feed Batch) and minute bars (Live Feed) are built in; see the README's Alpaca sections. Keys stay in `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY`.
