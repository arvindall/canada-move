# Canada portfolio tracker

This project is currently tuned for a **Phase 1 Canada arrival-day snapshot**:

- authoritative for **arrival-date holdings quantity and FMV by ticker in USD and CAD**
- based on a **fresh same-day Fidelity positions export** plus recorded FX
- still **provisional for detailed lot reconstruction** on positions that were traded directly in Fidelity

The main evidence pack for Phase 1 is:

- fresh Fidelity export
- snapshot summary CSV
- snapshot audit JSON

## Current scope

### Phase 1
Use this project to answer:

> What did I hold on my Canada arrival date, and what was it worth in USD and CAD?

### Not yet exact
This project does **not yet** fully reconstruct true tax lots across Robinhood + Fidelity activity.

- Robinhood activity is parsed from trade history
- Fidelity is currently used as the **authoritative current holdings source**
- some lot rows are still scaled or synthetic when Fidelity-origin trade history is missing

## Input files

Raw inputs live under `data/raw/brokerage/`.

Required:

- `data/raw/brokerage/robinhood_activity_full_account.csv`
- `data/raw/brokerage/fidelity_current_positions.csv`

## Important move-day rule

On the actual Canada arrival date:

1. export a **fresh Fidelity positions CSV**
2. save trade confirms / screenshots / PDF backup
3. copy the fresh export into `data/raw/brokerage/fidelity_current_positions.csv`
4. run the scripts below

Do **not** reuse an older Fidelity export for the final arrival snapshot.

## Run order

Run the Python scripts directly.

```bash
python3 scripts/parse_robinhood_activity.py
python3 scripts/build_lots_master_from_holdings_and_history.py
```

### Freeze a trading-day arrival snapshot

```bash
python3 scripts/freeze_canada_arrival_snapshot.py \
  --arrival-date 2026-06-01 \
  --input data/parsed/lots_master.csv \
  --fidelity-same-day data/raw/brokerage/fidelity_current_positions.csv \
  --weekend-policy strict \
  --fail-on-manual-review \
  --fail-on-stale-fidelity-export
```

### Freeze a weekend / holiday arrival snapshot

```bash
python3 scripts/freeze_canada_arrival_snapshot.py \
  --arrival-date 2026-06-01 \
  --input data/parsed/lots_master.csv \
  --fidelity-same-day data/raw/brokerage/fidelity_current_positions.csv \
  --weekend-policy prior-business-day \
  --fail-on-manual-review \
  --fail-on-stale-fidelity-export
```

### Validate the frozen snapshot

```bash
python3 scripts/validate_snapshot_output.py \
  --input data/parsed/snapshots/lots_master_canada_snapshot_2026-06-01.csv \
  --fail-on-manual-review
```

## Stale Fidelity export safeguard

`freeze_canada_arrival_snapshot.py` now inspects the Fidelity CSV footer line:

```text
Date downloaded Mar-18-2026 7:25 a.m ET
```

Behavior:

- if the export date **matches** `--arrival-date`, the run proceeds normally
- if the export date **predates** `--arrival-date`:
  - the script prints a warning in normal mode
  - the warning is written into the audit JSON and footer note
  - pricing falls back to market data instead of stale Fidelity prices
- with `--fail-on-stale-fidelity-export`, the run aborts immediately

## Outputs

### Parsed intermediates

- `data/parsed/robinhood_activity_normalized.csv`
- `data/parsed/robinhood_buy_lots.csv`
- `data/parsed/robinhood_sell_activity.csv`
- `data/parsed/lots_master.csv`
- `data/parsed/reconciliation_summary.csv`

### Final Phase 1 snapshot artifacts

Generated under `data/parsed/snapshots/`:

- `lots_master_canada_snapshot_<ARRIVAL_DATE>.csv`
- `lots_master_canada_snapshot_summary_<ARRIVAL_DATE>.csv`
- `lots_master_canada_snapshot_audit_<ARRIVAL_DATE>.json`

## What to review after a run

### 1. Validation output

You want to see:

- `manual_review_rows=0`
- `package_note_rows=1`
- `other_status_rows=0`

### 2. Summary CSV

This is the primary Phase 1 artifact.

Check:

- ticker
- total quantity
- arrival-day USD price
- USD FMV
- CAD FMV

### 3. Audit JSON

This records:

- arrival date
- valuation date
- FX source and FX date
- Fidelity export footer date
- Fidelity freshness status
- full Phase 1 package note

## Footer note added to outputs

Both the detailed snapshot CSV and the summary CSV now append a final footer row with:

- `account = NOTE`
- `ticker = __PACKAGE_NOTE__`
- `snapshot_status = PACKAGE_NOTE`

The note text states:

> For Phase 1: This package is authoritative for arrival-date holdings quantity and FMV by ticker in USD and CAD, using same-day Fidelity positions and recorded FX, while detailed lot reconstruction remains provisional for some Fidelity-origin trades. Recommendation: Use June move day + fresh Fidelity export + summary CSV + audit JSON as your formal Canada basis evidence pack.

If the Fidelity export is stale, that warning is appended to the footer note as well.

## Recommended evidence pack

Keep these together in a dated folder for the arrival date:

- fresh Fidelity positions CSV
- Fidelity PDF or screenshots
- recent Fidelity trade confirmations
- snapshot CSV
- snapshot summary CSV
- snapshot audit JSON

## Current limitation

This repo currently has an `n8n_canada_asset_agant.json` workflow file, but the referenced `run_action.py` wrapper is not present here.

For now, use the Python scripts directly as documented above.
