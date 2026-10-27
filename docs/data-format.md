# Quantarium's research data format (`quantarium-data-v1`)

Quantarium supplies compatibility, not data. It ships no market data and licenses none. You bring data you bought or gathered, convert it into this format, and Quantarium checks it and turns it into a scenario. Whether the data is complete and accurate, including whether it is free of survivorship bias, is your responsibility. Quantarium records what you declare and shows it with every result.

A data folder holds up to four files. [`examples/data/fictional_index`](../examples/data/fictional_index) is a complete example with invented companies and prices.

## `source.json` (required)

```json
{
  "format": "quantarium-data-v1",
  "supplier": "Who the data came from",
  "license_note": "What its terms allow, in your words",
  "survivorship": "survivorship_free",
  "description": "Optional",
  "as_of": "2031-08-15"
}
```

| Key | Meaning |
| --- | --- |
| `format` | Always `quantarium-data-v1`. |
| `supplier` | Required text: the vendor, broker, or your own process. |
| `license_note` | Optional text. Quantarium never redistributes your data; this note reminds you and anyone you share results with of its terms. |
| `survivorship` | `survivorship_free` if the data includes securities that were delisted or left the index during the period; `survivors_only` if it holds only securities that still trade (most free downloads); `unknown` if you are not sure. The default is `unknown`. |
| `description`, `as_of` | Optional: a description, and the date the data was taken. |

No other keys are allowed, and text fields are limited to 500 characters.

## `prices.csv` (required)

`date,symbol,open,high,low,close,volume`, one row per symbol per session, as in a Static price file. The difference is that a symbol may start after the first session and stop before the last. Between its first and last bar it needs a bar on every session; trading halts are not modelled, so fill or cut such gaps before importing. Symbols use `A`–`Z`, `0`–`9`, `.` and `-`, up to 20 characters.

## `delistings.csv` (optional)

`symbol,date,price,reason`

- `date`: the first session on which the symbol no longer trades. It must come after its last bar.
- `price`: what a holder received per share (a takeover price, or a bankruptcy recovery), or empty to use the last close.
- `reason`: free text, such as `bankruptcy` or `cash takeover`.

A symbol whose prices stop before the last session without a delisting row is reported as a warning; a holding in it is closed at its last close.

## `membership.csv` (optional)

`index,symbol,from,through`: one row per period of membership, `through` being the last day of membership, or empty while it continues. A symbol can leave and rejoin with a second row; periods of the same symbol in the same index may not overlap. Members without prices are reported as a warning.

## Commands

```bash
py -m quantarium data check path/to/folder
```

```bash
py -m quantarium data import path/to/folder scenarios/my_universe --name my_universe
```

`check` validates the folder and prints a summary: symbols, sessions, late listings, delistings, indexes, file hashes, and warnings. `import` makes a historical scenario that copies the files with their hashes and records the source and your survivorship declaration; a scenario whose files change afterwards no longer verifies.

## Running it

Suites and the Lab replay an imported scenario as a changing universe ([ADR-0039](adr/0039-changing-universes.md)). A cell lists the symbols it may ever hold; with `"index": "NAME"` it holds only members of that index as of each close. Holdings in a delisted symbol are paid out at the first session without a bar, at the delisting price or the last close. A Static config can use a data folder's files directly:

```json
{"data_path": "data/my_universe/prices.csv", "delistings_path": "data/my_universe/delistings.csv",
 "membership_path": "data/my_universe/membership.csv", "database_path": "runs/my_universe.sqlite",
 "strategy_root": "strategies",
 "cells": [{"name": "index_equal_weight", "mode": "complex", "index": "FIC5", "initial_cash": 100000,
            "symbols": ["ALFA", "BRVO", "CHRL", "DLTA", "ECHO", "FXTR"],
            "strategies": [{"entrypoint": "strategies:EqualWeight"}]}]}
```

Feed Batch and Live Feed still replay a fixed set of symbols.

## Limits

All files are treated as untrusted: `prices.csv` up to 1 GiB, other tables up to 64 MiB and a million rows, `source.json` up to 64 KiB, and at most 20,000 symbols. Numbers must be finite; reading a folder never runs code.
