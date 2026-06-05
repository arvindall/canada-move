#!/usr/bin/env python3

import argparse
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

BASE_REQUIRED_COLUMNS = [
    "account",
    "ticker",
    "acquisition_date",
    "open_quantity",
    "unit_cost_usd",
    "total_cost_basis_usd",
    "source",
]

SNAPSHOT_COLUMNS = [
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

YAHOO_TICKER_MAP = {
    "BRK.B": "BRK-B",
    "BRKB": "BRK-B",
    "BF.B": "BF-B",
}

VALET_URL = "https://www.bankofcanada.ca/valet/observations/FXUSDCAD/json"

BASE = Path(__file__).resolve().parents[1]
RAW_BROKERAGE_DIR = BASE / "data" / "raw" / "brokerage"
PARSED_DIR = BASE / "data" / "parsed"
SNAPSHOTS_DIR = PARSED_DIR / "snapshots"
LOTS_MASTER_FILE = PARSED_DIR / "lots_master.csv"

PACKAGE_NOTE_STATUS = "PACKAGE_NOTE"
PACKAGE_NOTE_ACCOUNT = "NOTE"
PACKAGE_NOTE_TICKER = "__PACKAGE_NOTE__"
PHASE1_PACKAGE_NOTE = (
    "For Phase 1: This package is authoritative for arrival-date holdings quantity "
    "and FMV by ticker in USD and CAD, using same-day Fidelity positions and recorded "
    "FX, while detailed lot reconstruction remains provisional for some "
    "Fidelity-origin trades."
)
PHASE1_RECOMMENDATION_NOTE = (
    "Recommendation: Use June move day + fresh Fidelity export + summary CSV + audit "
    "JSON as your formal Canada basis evidence pack."
)
PHASE1_FULL_NOTE = f"{PHASE1_PACKAGE_NOTE} {PHASE1_RECOMMENDATION_NOTE}"
FIDELITY_DATE_DOWNLOADED_RE = re.compile(
    r"Date downloaded\s+([A-Za-z]{3}-\d{1,2}-\d{4})(?:\s+.+)?$"
)


def is_blank(v):
    return pd.isna(v) or str(v).strip() == ""


def normalize_ticker(ticker: str) -> str:
    t = str(ticker).strip().upper()
    return YAHOO_TICKER_MAP.get(t, t)


def ensure_schema(df: pd.DataFrame) -> pd.DataFrame:
    for col in SNAPSHOT_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    return df


def validate_base_columns(df: pd.DataFrame):
    missing = [c for c in BASE_REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")


def row_already_frozen(row: pd.Series) -> bool:
    return str(row.get("snapshot_status")).strip().upper() == "FROZEN"


def validate_arrival_date(arrival_date: str) -> str:
    parsed = datetime.strptime(arrival_date, "%Y-%m-%d").date()
    today_utc = datetime.now(timezone.utc).date()
    if parsed > today_utc:
        raise ValueError(f"arrival_date {arrival_date} is in the future")
    return arrival_date


def is_weekend(date_str: str) -> bool:
    return datetime.strptime(date_str, "%Y-%m-%d").weekday() >= 5


def prior_business_day(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


def resolve_valuation_date(arrival_date: str, weekend_policy: str):
    if weekend_policy == "prior-business-day" and is_weekend(arrival_date):
        valuation_date = prior_business_day(arrival_date)
        note = (
            f"weekend arrival {arrival_date}; "
            f"used prior business day {valuation_date} for stock price and FX"
        )
        return valuation_date, note
    return arrival_date, ""


def clean_money_series(s: pd.Series) -> pd.Series:
    return pd.to_numeric(
        s.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("$", "", regex=False)
        .str.strip(),
        errors="coerce",
    )


def combine_notes(*parts):
    cleaned = []
    for p in parts:
        if p is None:
            continue
        try:
            if pd.isna(p):
                continue
        except TypeError:
            pass
        text = str(p).strip()
        if text:
            cleaned.append(text)
    return "; ".join(cleaned) if cleaned else pd.NA


def load_fidelity_prices(path, ticker_col="Symbol", price_col="Last Price"):
    if not path:
        return {}

    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Fidelity file not found: {csv_path}")

    df = pd.read_csv(csv_path)

    missing = [c for c in [ticker_col, price_col] if c not in df.columns]
    if missing:
        raise ValueError(
            f"Missing Fidelity columns: {missing}. "
            f"Available columns: {list(df.columns)}"
        )

    tickers = df[ticker_col].astype(str).str.strip().str.upper()
    prices = clean_money_series(df[price_col])

    out = {}
    for ticker, price in zip(tickers, prices):
        if ticker and ticker != "NAN" and pd.notna(price):
            out[ticker] = float(price)

    return out


def parse_fidelity_export_metadata(path):
    if not path:
        return {
            "file": None,
            "export_date": None,
            "raw_footer_line": None,
            "parse_status": "not_provided",
        }

    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Fidelity file not found: {csv_path}")

    with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
        for raw_line in reversed(f.readlines()):
            line = raw_line.strip().strip("\"")
            m = FIDELITY_DATE_DOWNLOADED_RE.search(line)
            if not m:
                continue

            raw_date = m.group(1)
            parsed_date = datetime.strptime(raw_date, "%b-%d-%Y").date().isoformat()
            return {
                "file": str(csv_path),
                "export_date": parsed_date,
                "raw_footer_line": line,
                "parse_status": "parsed",
            }

    return {
        "file": str(csv_path),
        "export_date": None,
        "raw_footer_line": None,
        "parse_status": "date_downloaded_footer_not_found",
    }


def assess_fidelity_export_freshness(metadata, arrival_date: str):
    arrival = datetime.strptime(arrival_date, "%Y-%m-%d").date()
    export_date = metadata.get("export_date")

    if not export_date:
        return {
            "status": "unknown",
            "is_stale": False,
            "warning_note": (
                "WARNING: Could not verify Fidelity export freshness because the "
                "Date downloaded footer was not found."
            ),
        }

    exported = datetime.strptime(export_date, "%Y-%m-%d").date()

    if exported < arrival:
        return {
            "status": "stale",
            "is_stale": True,
            "warning_note": (
                f"WARNING: Fidelity export date {export_date} predates arrival date "
                f"{arrival_date}; refresh the same-day Fidelity positions export."
            ),
        }

    if exported > arrival:
        return {
            "status": "after_arrival",
            "is_stale": False,
            "warning_note": (
                f"NOTE: Fidelity export date {export_date} is after arrival date "
                f"{arrival_date}; confirm you intended to use a post-arrival export."
            ),
        }

    return {
        "status": "same_day",
        "is_stale": False,
        "warning_note": "",
    }


def build_package_note_text(extra_note: str = "") -> str:
    parts = [PHASE1_FULL_NOTE]
    if extra_note:
        parts.append(extra_note)
    return " ".join(parts)


def fetch_fx_usd_cad(target_date: str):
    def extract(payload):
        rows = []
        for obs in payload.get("observations", []):
            d = obs.get("d")
            raw = obs.get("FXUSDCAD", {}).get("v")
            if d and raw not in (None, ""):
                rows.append((d, float(raw)))
        return rows

    params = {"start_date": target_date, "end_date": target_date}
    r = requests.get(VALET_URL, params=params, timeout=30)
    r.raise_for_status()
    exact = extract(r.json())
    if exact:
        d, v = exact[0]
        return {
            "value": v,
            "source_type": "bank_of_canada_valet",
            "source_date": d,
            "note": "" if d == target_date else f"fx from {d}, requested {target_date}",
        }

    start = (datetime.strptime(target_date, "%Y-%m-%d").date() - timedelta(days=7)).isoformat()
    params = {"start_date": start, "end_date": target_date}
    r = requests.get(VALET_URL, params=params, timeout=30)
    r.raise_for_status()

    usable = [x for x in extract(r.json()) if x[0] <= target_date]
    if not usable:
        raise ValueError(f"No FXUSDCAD observation found on or before {target_date}")

    d, v = usable[-1]
    return {
        "value": v,
        "source_type": "bank_of_canada_valet",
        "source_date": d,
        "note": "" if d == target_date else f"fx from prior business day {d}, requested {target_date}",
    }


def fetch_yfinance_close_exact_date(ticker: str, target_date: str):
    start = datetime.strptime(target_date, "%Y-%m-%d").date()
    end = start + timedelta(days=1)
    yt = normalize_ticker(ticker)

    hist = yf.Ticker(yt).history(
        start=start.isoformat(),
        end=end.isoformat(),
        interval="1d",
        auto_adjust=False,
        actions=False,
        raise_errors=False,
    )

    if hist.empty or "Close" not in hist.columns:
        return None

    closes = hist["Close"].dropna()
    if closes.empty:
        return None

    ts = closes.index[-1]
    src_date = ts.date().isoformat() if hasattr(ts, "date") else str(ts)

    if src_date != target_date:
        return None

    return {
        "price": float(closes.iloc[-1]),
        "source_type": "yfinance_exact_date",
        "source_date": src_date,
        "note": "",
    }


def build_summary(df: pd.DataFrame) -> pd.DataFrame:
    temp = df.copy()
    if "snapshot_status" in temp.columns:
        temp = temp[
            temp["snapshot_status"].astype(str).str.strip().str.upper() != PACKAGE_NOTE_STATUS
        ].copy()
    temp["open_quantity"] = pd.to_numeric(temp["open_quantity"], errors="coerce")
    temp["arrival_day_price_usd"] = pd.to_numeric(temp["arrival_day_price_usd"], errors="coerce")
    temp["arrival_day_fmv_usd"] = pd.to_numeric(temp["arrival_day_fmv_usd"], errors="coerce")
    temp["arrival_day_fmv_cad"] = pd.to_numeric(temp["arrival_day_fmv_cad"], errors="coerce")

    summary_cols = [
        "account",
        "ticker",
        "price_source_type",
        "snapshot_status",
        "lots",
        "open_quantity",
        "arrival_day_price_usd",
        "arrival_day_fmv_usd",
        "arrival_day_fmv_cad",
        "package_note",
    ]

    if temp.empty:
        return pd.DataFrame(columns=summary_cols)

    summary = (
        temp.groupby(
            ["account", "ticker", "price_source_type", "snapshot_status"],
            dropna=False,
        )
        .agg(
            lots=("ticker", "count"),
            open_quantity=("open_quantity", "sum"),
            arrival_day_price_usd=("arrival_day_price_usd", "last"),
            arrival_day_fmv_usd=("arrival_day_fmv_usd", "sum"),
            arrival_day_fmv_cad=("arrival_day_fmv_cad", "sum"),
        )
        .reset_index()
        .sort_values(["account", "ticker"])
    )
    summary["package_note"] = pd.NA
    return summary[summary_cols]


def append_snapshot_package_note_row(
    df: pd.DataFrame, arrival_date: str, run_id: str, frozen_at: str, package_note_text: str
) -> pd.DataFrame:
    out = df.copy()
    for col in BASE_REQUIRED_COLUMNS + SNAPSHOT_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA

    note_row = {col: pd.NA for col in out.columns}
    note_row["account"] = PACKAGE_NOTE_ACCOUNT
    note_row["ticker"] = PACKAGE_NOTE_TICKER
    note_row["source"] = "phase1_guidance"
    note_row["canada_arrival_date"] = arrival_date
    note_row["snapshot_run_id"] = run_id
    note_row["snapshot_frozen_at"] = frozen_at
    note_row["snapshot_status"] = PACKAGE_NOTE_STATUS
    note_row["snapshot_notes"] = package_note_text

    note_df = pd.DataFrame([note_row], columns=out.columns)
    return pd.concat([out, note_df], ignore_index=True)


def append_summary_package_note_row(df: pd.DataFrame, package_note_text: str) -> pd.DataFrame:
    out = df.copy()
    if "package_note" not in out.columns:
        out["package_note"] = pd.NA

    note_row = {col: pd.NA for col in out.columns}
    note_row["account"] = PACKAGE_NOTE_ACCOUNT
    note_row["ticker"] = PACKAGE_NOTE_TICKER
    note_row["snapshot_status"] = PACKAGE_NOTE_STATUS
    note_row["package_note"] = package_note_text

    note_df = pd.DataFrame([note_row], columns=out.columns)
    return pd.concat([out, note_df], ignore_index=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--input",
        default=str(LOTS_MASTER_FILE),
        help=f"Path to lots_master.csv (default: {LOTS_MASTER_FILE})",
    )
    ap.add_argument("--arrival-date", required=True, help="YYYY-MM-DD")
    ap.add_argument(
        "--output-dir",
        default=str(SNAPSHOTS_DIR),
        help=f"Output directory for snapshot files (default: {SNAPSHOTS_DIR})",
    )
    ap.add_argument(
        "--fidelity-same-day",
        help="Optional Fidelity positions CSV; if relative, resolves under data/raw/brokerage",
    )
    ap.add_argument("--fidelity-ticker-col", default="Symbol")
    ap.add_argument("--fidelity-price-col", default="Last Price")
    ap.add_argument("--force", action="store_true", help="Re-freeze rows already frozen")
    ap.add_argument(
        "--fail-on-manual-review",
        action="store_true",
        help="Exit non-zero if any rows end in NEEDS_MANUAL_REVIEW",
    )
    ap.add_argument(
        "--weekend-policy",
        choices=["strict", "prior-business-day"],
        default="strict",
        help=(
            "Weekend handling: strict=manual review for weekend stock prices; "
            "prior-business-day=use Friday prices/FX while keeping canada_arrival_date unchanged"
        ),
    )
    ap.add_argument(
        "--fail-on-stale-fidelity-export",
        action="store_true",
        help="Exit non-zero if the Fidelity export footer date predates the arrival date",
    )
    args = ap.parse_args()

    arrival_date = validate_arrival_date(args.arrival_date)
    valuation_date, weekend_policy_note = resolve_valuation_date(
        arrival_date, args.weekend_policy
    )
    run_id = f"canada_snapshot_{arrival_date}_{uuid.uuid4().hex[:8]}"
    frozen_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    input_path = Path(args.input).expanduser()
    if not input_path.is_absolute():
        input_path = (BASE / input_path).resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    output_dir = Path(args.output_dir).expanduser()
    if not output_dir.is_absolute():
        output_dir = (BASE / output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    fidelity_same_day_path = None
    if args.fidelity_same_day:
        fidelity_same_day_path = Path(args.fidelity_same_day).expanduser()
        if not fidelity_same_day_path.is_absolute():
            candidate_under_raw = (RAW_BROKERAGE_DIR / fidelity_same_day_path).resolve()
            candidate_under_base = (BASE / fidelity_same_day_path).resolve()

            if candidate_under_raw.exists():
                fidelity_same_day_path = candidate_under_raw
            else:
                fidelity_same_day_path = candidate_under_base

        if not fidelity_same_day_path.exists():
            raise FileNotFoundError(f"Fidelity file not found: {fidelity_same_day_path}")

    fidelity_export_metadata = parse_fidelity_export_metadata(fidelity_same_day_path)
    fidelity_export_freshness = assess_fidelity_export_freshness(
        fidelity_export_metadata, arrival_date
    )
    package_note_text = build_package_note_text(
        fidelity_export_freshness["warning_note"]
    )

    if args.fail_on_stale_fidelity_export and fidelity_export_freshness["is_stale"]:
        raise SystemExit(f"ERROR: {fidelity_export_freshness['warning_note']}")

    df = pd.read_csv(input_path)
    validate_base_columns(df)
    df = ensure_schema(df)

    fx = fetch_fx_usd_cad(valuation_date)
    fidelity_prices = (
        load_fidelity_prices(
            str(fidelity_same_day_path),
            ticker_col=args.fidelity_ticker_col,
            price_col=args.fidelity_price_col,
        )
        if fidelity_same_day_path
        else {}
    )
    use_fidelity_same_day = (
        bool(fidelity_same_day_path)
        and valuation_date == arrival_date
        and fidelity_export_freshness["status"] == "same_day"
    )

    yf_cache = {}

    audit = {
        "run_id": run_id,
        "arrival_date": arrival_date,
        "valuation_date": valuation_date,
        "weekend_policy": args.weekend_policy,
        "valuation_policy_note": weekend_policy_note,
        "frozen_at": frozen_at,
        "input_file": str(input_path),
        "output_dir": str(output_dir),
        "fidelity_same_day_file": str(fidelity_same_day_path) if fidelity_same_day_path else None,
        "fidelity_ticker_col": args.fidelity_ticker_col,
        "fidelity_price_col": args.fidelity_price_col,
        "fidelity_export_date": fidelity_export_metadata["export_date"],
        "fidelity_export_footer_line": fidelity_export_metadata["raw_footer_line"],
        "fidelity_export_parse_status": fidelity_export_metadata["parse_status"],
        "fidelity_export_freshness_status": fidelity_export_freshness["status"],
        "fidelity_export_freshness_warning": fidelity_export_freshness["warning_note"],
        "fx_source_type": fx["source_type"],
        "fx_source_date": fx["source_date"],
        "fx_value": fx["value"],
        "fx_note": fx["note"],
        "phase1_package_note": PHASE1_PACKAGE_NOTE,
        "phase1_recommendation_note": PHASE1_RECOMMENDATION_NOTE,
        "phase1_package_note_full": package_note_text,
        "rows_total": int(len(df)),
        "rows_frozen": 0,
        "rows_manual_review": 0,
        "rows_skipped": 0,
        "tickers": {},
    }

    result_rows = []

    for _, row in df.iterrows():
        row = row.copy()
        ticker = str(row.get("ticker", "")).strip().upper()

        if row_already_frozen(row) and not args.force:
            result_rows.append(row)
            audit["rows_skipped"] += 1
            audit["tickers"].setdefault(ticker or "<blank>", []).append("skipped_already_frozen")
            continue

        row["canada_arrival_date"] = arrival_date
        row["usd_cad_fx_on_arrival"] = fx["value"]
        row["fx_source_type"] = fx["source_type"]
        row["fx_source_date"] = fx["source_date"]
        row["snapshot_run_id"] = run_id
        row["snapshot_frozen_at"] = frozen_at

        if ticker in ("", "NAN"):
            row["arrival_day_price_usd"] = pd.NA
            row["arrival_day_fmv_usd"] = pd.NA
            row["arrival_day_fmv_cad"] = pd.NA
            row["price_source_type"] = "manual_review"
            row["price_source_date"] = pd.NA
            row["snapshot_status"] = "NEEDS_MANUAL_REVIEW"
            row["snapshot_notes"] = combine_notes("missing ticker", fx["note"], weekend_policy_note)
            result_rows.append(row)
            audit["rows_manual_review"] += 1
            audit["tickers"].setdefault("<blank>", []).append("missing_ticker")
            continue

        qty = pd.to_numeric(row["open_quantity"], errors="coerce")

        if pd.isna(qty):
            row["arrival_day_price_usd"] = pd.NA
            row["arrival_day_fmv_usd"] = pd.NA
            row["arrival_day_fmv_cad"] = pd.NA
            row["price_source_type"] = "manual_review"
            row["price_source_date"] = pd.NA
            row["snapshot_status"] = "NEEDS_MANUAL_REVIEW"
            row["snapshot_notes"] = combine_notes("invalid open_quantity", fx["note"], weekend_policy_note)
            result_rows.append(row)
            audit["rows_manual_review"] += 1
            audit["tickers"].setdefault(ticker, []).append("invalid_open_quantity")
            continue

        if use_fidelity_same_day and ticker in fidelity_prices:
            price_meta = {
                "price": fidelity_prices[ticker],
                "source_type": "fidelity_same_day",
                "source_date": arrival_date,
                "note": "fidelity same-day export",
            }
        else:
            if ticker not in yf_cache:
                yf_cache[ticker] = fetch_yfinance_close_exact_date(ticker, valuation_date)
            price_meta = yf_cache[ticker]

        if not price_meta:
            if args.weekend_policy == "prior-business-day" and is_weekend(arrival_date):
                note = f"missing prior-business-day price for {valuation_date}"
                audit_code = "missing_prior_business_day_price"
            else:
                note = "missing exact-date price"
                if is_weekend(arrival_date):
                    note = f"{note}; arrival date falls on weekend/non-trading day"
                audit_code = "missing_exact_date_price"

            row["arrival_day_price_usd"] = pd.NA
            row["arrival_day_fmv_usd"] = pd.NA
            row["arrival_day_fmv_cad"] = pd.NA
            row["price_source_type"] = "manual_review"
            row["price_source_date"] = pd.NA
            row["snapshot_status"] = "NEEDS_MANUAL_REVIEW"
            row["snapshot_notes"] = combine_notes(note, fx["note"], weekend_policy_note)
            result_rows.append(row)
            audit["rows_manual_review"] += 1
            audit["tickers"].setdefault(ticker, []).append(audit_code)
            continue

        arrival_day_price_usd = round(float(price_meta["price"]), 4)
        arrival_day_fmv_usd = round(float(qty) * float(price_meta["price"]), 2)
        arrival_day_fmv_cad = round(arrival_day_fmv_usd * float(fx["value"]), 2)

        row["arrival_day_price_usd"] = arrival_day_price_usd
        row["arrival_day_fmv_usd"] = arrival_day_fmv_usd
        row["arrival_day_fmv_cad"] = arrival_day_fmv_cad
        row["price_source_type"] = price_meta["source_type"]
        row["price_source_date"] = price_meta["source_date"]
        row["snapshot_status"] = "FROZEN"
        row["snapshot_notes"] = combine_notes(
            price_meta.get("note"),
            fx["note"],
            weekend_policy_note,
        )

        result_rows.append(row)
        audit["rows_frozen"] += 1
        audit["tickers"].setdefault(ticker, []).append(price_meta["source_type"])

    out_df = pd.DataFrame(result_rows)
    out_df = append_snapshot_package_note_row(
        out_df, arrival_date, run_id, frozen_at, package_note_text
    )
    summary_df = append_summary_package_note_row(build_summary(out_df), package_note_text)

    snapshot_csv = output_dir / f"lots_master_canada_snapshot_{arrival_date}.csv"
    summary_csv = output_dir / f"lots_master_canada_snapshot_summary_{arrival_date}.csv"
    audit_json = output_dir / f"lots_master_canada_snapshot_audit_{arrival_date}.json"

    out_df.to_csv(snapshot_csv, index=False)
    summary_df.to_csv(summary_csv, index=False)

    with open(audit_json, "w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2)

    print(snapshot_csv)
    print(summary_csv)
    print(audit_json)
    if fidelity_export_freshness["warning_note"]:
        print(fidelity_export_freshness["warning_note"])

    if args.fail_on_manual_review and audit["rows_manual_review"] > 0:
        raise SystemExit(2)


if __name__ == "__main__":
    main()