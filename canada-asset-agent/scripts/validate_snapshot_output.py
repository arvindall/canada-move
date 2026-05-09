#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parents[1]
PARSED_DIR = BASE / "data" / "parsed"
SNAPSHOTS_DIR = PARSED_DIR / "snapshots"

REQUIRED_COLUMNS = [
    "account",
    "ticker",
    "acquisition_date",
    "open_quantity",
    "unit_cost_usd",
    "total_cost_basis_usd",
    "source",
    "canada_arrival_date",
    "arrival_day_price_usd",
    "arrival_day_fmv_usd",
    "usd_cad_fx_on_arrival",
    "arrival_day_fmv_cad",
    "price_source_type",
    "price_source_date",
    "fx_source_type",
    "fx_source_date",
    "snapshot_run_id",
    "snapshot_frozen_at",
    "snapshot_status",
    "snapshot_notes",
]

PACKAGE_NOTE_STATUS = "PACKAGE_NOTE"


def resolve_input_path(raw_input: str) -> Path:
    path = Path(raw_input).expanduser()

    if path.is_absolute():
        return path

    if path.parts and path.parts[0] in (".", ".."):
        return (BASE / path).resolve()

    candidate_snapshot = (SNAPSHOTS_DIR / path).resolve()
    if candidate_snapshot.exists():
        return candidate_snapshot

    return (PARSED_DIR / path).resolve()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--input",
        required=True,
        help="Snapshot CSV path; bare filenames resolve under data/parsed/snapshots first",
    )
    ap.add_argument("--limit", type=int, default=10, help="Sample rows to print")
    ap.add_argument(
        "--fail-on-manual-review",
        action="store_true",
        help="Exit non-zero if any rows have snapshot_status=NEEDS_MANUAL_REVIEW",
    )
    args = ap.parse_args()

    input_path = resolve_input_path(args.input)
    if not input_path.exists():
        print(f"ERROR: file not found: {input_path}", file=sys.stderr)
        raise SystemExit(2)

    df = pd.read_csv(input_path)

    print(f"file={input_path}")
    print(f"rows={len(df)}")
    print("columns:")
    for col in df.columns:
        print(f"  - {col}")

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        print(f"\nERROR: missing required columns: {missing}", file=sys.stderr)
        raise SystemExit(2)

    statuses = df["snapshot_status"].astype(str).str.strip().str.upper()
    package_note = df[statuses.eq(PACKAGE_NOTE_STATUS)].copy()
    working = df[~statuses.eq(PACKAGE_NOTE_STATUS)].copy()
    working_statuses = working["snapshot_status"].astype(str).str.strip().str.upper()

    manual_review = working[working_statuses.eq("NEEDS_MANUAL_REVIEW")].copy()
    frozen = working[working_statuses.eq("FROZEN")].copy()
    other = working[~working_statuses.isin(["FROZEN", "NEEDS_MANUAL_REVIEW"])].copy()

    print(f"\nfrozen_rows={len(frozen)}")
    print(f"manual_review_rows={len(manual_review)}")
    print(f"package_note_rows={len(package_note)}")
    print(f"other_status_rows={len(other)}")

    if not package_note.empty:
        cols = [
            c for c in [
                "account",
                "ticker",
                "snapshot_status",
                "snapshot_notes",
            ] if c in package_note.columns
        ]
        print("\npackage_note_sample:")
        print(package_note[cols].head(args.limit).to_string(index=False))

    if not manual_review.empty:
        cols = [
            c for c in [
                "account",
                "ticker",
                "open_quantity",
                "price_source_type",
                "price_source_date",
                "snapshot_status",
                "snapshot_notes",
            ] if c in manual_review.columns
        ]
        print("\nmanual_review_sample:")
        print(manual_review[cols].head(args.limit).to_string(index=False))

    if not other.empty:
        cols = [
            c for c in [
                "account",
                "ticker",
                "snapshot_status",
                "snapshot_notes",
            ] if c in other.columns
        ]
        print("\nother_status_sample:")
        print(other[cols].head(args.limit).to_string(index=False))

    ticker_summary = (
        working.groupby(["ticker", "snapshot_status"], dropna=False)
          .size()
          .reset_index(name="rows")
          .sort_values(["ticker", "snapshot_status"])
    )

    print("\nticker_status_sample:")
    print(ticker_summary.head(20).to_string(index=False))

    summary = {
        "rows_total": int(len(df)),
        "rows_frozen": int(len(frozen)),
        "rows_manual_review": int(len(manual_review)),
        "rows_package_note": int(len(package_note)),
        "rows_other_status": int(len(other)),
        "unique_tickers": int(working["ticker"].astype(str).str.strip().nunique()),
        "arrival_dates": sorted(working["canada_arrival_date"].dropna().astype(str).unique().tolist()),
        "snapshot_run_ids": sorted(working["snapshot_run_id"].dropna().astype(str).unique().tolist())[:5],
    }

    print("\nsummary:")
    for k, v in summary.items():
        print(f"{k}={v}")

    if args.fail_on_manual_review and len(manual_review) > 0:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
