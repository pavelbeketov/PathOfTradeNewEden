# New Eden: Black Market

A tool for finding market trades and planning a full loop or a short trading route through five EVE Online regions. It downloads market orders through EVE ESI, plans purchases and sales within a shared budget, cash reserve, and cargo capacity, then generates HTML reports with step-by-step trading plans.

> This is a market-data planner, not a trading bot: it does not log in to the game or execute trades. Orders and prices may change before you arrive.

## Features

- Downloads and validates market snapshots for The Forge, Sinq Laison, Metropolis, Heimatar, and Domain.
- Chooses trade quantities based on budget, cash reserve, cargo capacity, and reinvestment of proceeds along the route.
- Accounts for sales tax, fees, minimum profit thresholds, system security, and contraband.
- Can optimize the order of regional visits and choose trading hubs.
- Offers a simpler route with up to six station visits and up to three different items to buy at each stop.
- Generates an HTML report with trade stops, prices, quantities, route legs, and remaining cash and cargo.

## Requirements

- Windows to use `Run_trade.cmd` (the individual Python scripts can also be run on other operating systems).
- Python 3.12 or newer.
- Network access to EVE ESI while downloading market data.
- The SQLite SDE file `sqlite-latest.sqlite` in the project root. It provides item, station, system, and contraband data.

## Quick start

Open PowerShell in the project directory and install the dependencies:

```powershell
python -m pip install -r requirements.txt
```

Review `settings.json`, then run the full pipeline:

```powershell
python Run_trade.py
```

On Windows, you can also run `Run_trade.cmd`. It opens `results/arbitrage_report.html` when the pipeline completes successfully. The pipeline has three stages; if one fails, the remaining stages are skipped.

To run the stages individually:

```powershell
python Orders_in_a_region.py  # download and validate market snapshots
python Calculate_profit.py    # calculate trades and optimize the route
python Make_report.py         # generate the HTML report
```

Open `results/arbitrage_report.html` in a browser to view the full plan. Follow its short-route link, or open `results/short_route_report.html`, for the simpler plan. Both pages and intermediate files in `results/` are generated locally.

## Configuration

The main settings are in `settings.json` (standard JSON: no comments or trailing commas).

| Setting | Default | Description |
|---|---:|---|
| `budget_isk` | `40000000` | ISK available for the route |
| `cargo_m3` | `27000` | Cargo capacity, in m³ |
| `cash_reserve_isk` | `0` | Cash to keep untouched |
| `sales_tax_pct` | `7.5` | Sales tax, in percent |
| `buy_fee_pct`, `sell_fee_pct` | `0` | Additional trade fees, in percent |
| `min_profit_pct` | `1` | Minimum profit per order pair, in percent |
| `min_profit_per_unit_isk` | `0` | Minimum profit per item, in ISK |
| `route` | 5 regions and return | Each of the five regions must appear once; the route starts and ends in `The_Forge` |
| `optimize_region_order` | `true` | Try different orders for the four intermediate regions |
| `station_scope` | `"hubs"` | `hubs` selects major trade hubs; `region` includes eligible NPC stations in each region |
| `start_station_id` | `60003760` | Starting and ending station (Jita IV – Moon 4 – Caldari Navy Assembly Plant) |
| `min_system_security` | `0.5` | Minimum security for stations and route systems |
| `exclude_contraband` | `true` | Exclude items marked as contraband |
| `max_snapshot_age_minutes` | `30` | Maximum market snapshot age allowed for calculations and reports |
| `max_snapshot_skew_seconds` | `300` | Maximum timestamp difference between regional snapshots |
| `top_n` | `200` | Maximum number of options in trade tables; does not limit route search |
| `download_workers` | `6` | Parallel downloader requests (maximum 16) |
| `http_retries`, `http_timeout_seconds` | `7`, `30` | HTTP retry count and request timeout |
| `resolve_missing_types` | `true` | Fetch missing item names and volumes from ESI |
| `solver_time_limit_seconds` | `60` | Optimizer time limit for a promising region order |
| `solver_relative_gap` | `0.001` | Allowed relative optimizer gap (0.001 = 0.1%) |

Before a new run, update the budget or reserve to match your actual ISK balance. If you change settings after downloading market data, rerun the calculation and report stages. If the snapshots are stale, run the full pipeline again.

## Reading the plan

The full route starts and ends in The Forge. The planner selects trading stops in each of the other four regions and suggests selling any remaining cargo at the end. Travel between stops may pass through additional systems and regions. Use the checkboxes in the report to track completed actions manually.

The separate short-route page limits the plan to six station visits, including the first purchase stop and final sale stop. At each stop, buy up to three distinct item types; the number of units depends on the available ISK, cargo space, and order liquidity. Several price levels of the same item count as one item type. Sell the entire shipment at the next station before making the next purchases, reinvesting the proceeds. The route ends with an empty hold and may finish earlier when additional travel would reduce profit per jump.

The short route can start at any eligible station and does not require returning to the start or following the full loop's `route`, `optimize_region_order`, or `start_station_id`. It uses the budget, reserve, cargo limit, taxes, fees, profit thresholds, station scope, security, contraband, and snapshot requirements in `settings.json`. Its heuristic search prioritizes net profit per gate jump to favor less flying; optimality is not guaranteed. For stations in the same system, zero jumps are scored as one to keep comparisons finite. Six stops is a limit on station visits, not on the number of gate jumps.

After completing either plan, update `budget_isk` to your actual balance and run the pipeline again for a new plan based on refreshed orders. Completed-action checkboxes do not execute trades or automatically recalculate the route.

The planner optimizes total profit for the route, not profit per hour. Its calculations use order liquidity in the downloaded snapshots; they cannot guarantee that an order will still be available, that a trade will fill completely, or that a route will be safe. Before each trade, check the price, volume, minimum order volume, and hauling conditions in game. Sales tax and fees depend on your skills and character settings, so set the values in `settings.json` accordingly.

## Project structure

- `Run_trade.py`, `Run_trade.cmd` — run the full pipeline.
- `Orders_in_a_region.py` — download market snapshots from ESI.
- `Calculate_profit.py`, `route_optimizer.py`, `short_route_optimizer.py` — evaluate trades and plan the full and short routes.
- `Make_report.py`, `route_report.py` — generate the report.
- `market_data.py`, `trader_common.py`, `esi_client.py` — market data, settings, and shared utilities.
- `data/` — local snapshots and metadata.
- `results/` — calculation tables and HTML report.
- `tests/` — unit tests.

## License

No license is currently specified in this repository. Until one is added, the terms for using and distributing the code are undefined.
