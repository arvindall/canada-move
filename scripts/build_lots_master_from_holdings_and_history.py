from pathlib import Path
from io import StringIO
import re
import pandas as pd

BASE = Path(__file__).resolve().parents[1]

RAW_POSITIONS = BASE / 'data' / 'raw' / 'brokerage' / 'fidelity_current_positions.csv'
RAW_HISTORY = BASE / 'data' / 'raw' / 'brokerage' / 'robinhood_activity_full_account.csv'

PARSED_DIR = BASE / 'data' / 'parsed'
BUY_LOTS_FILE = PARSED_DIR / 'robinhood_buy_lots.csv'
SELLS_FILE = PARSED_DIR / 'robinhood_sell_activity.csv'
OUT_FILE = PARSED_DIR / 'lots_master.csv'
RECON_FILE = PARSED_DIR / 'reconciliation_summary.csv'
PARSED_DIR.mkdir(parents=True, exist_ok=True)


def clean_money(v):
    if pd.isna(v) or str(v).strip() == '':
        return 0.0
    s = str(v).strip().replace('$', '').replace(',', '').replace('+', '')
    if s.startswith('(') and s.endswith(')'):
        s = '-' + s[1:-1]
    return float(s)


def clean_num(x):
    if pd.isna(x):
        return None
    s = str(x).strip().replace(',', '')
    m = re.search(r'-?\d+(?:\.\d+)?', s)
    return float(m.group()) if m else None


def load_current_positions(csv_file):
    with open(csv_file, 'r', encoding='utf-8', errors='replace') as f:
        lines = f.readlines()

    cleaned = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith('"The data and information'):
            continue
        if s.startswith('"Brokerage services are provided'):
            continue
        if s.startswith('"Date downloaded'):
            continue
        if line.rstrip('\n').endswith(','):
            line = line.rstrip('\n').rstrip(',') + '\n'
        cleaned.append(line)

    df = pd.read_csv(StringIO(''.join(cleaned)), engine='python')
    df.columns = [c.strip() for c in df.columns]
    return df


def normalize_positions(df):
    rename = {
        'Account Name': 'account_name',
        'Symbol': 'ticker',
        'Description': 'description',
        'Quantity': 'quantity',
        'Last Price': 'current_price_usd',
        'Current Value': 'current_value_usd',
        'Cost Basis Total': 'cost_basis_total_usd',
        'Average Cost Basis': 'average_cost_basis_usd',
        'Type': 'type'
    }
    df = df.rename(columns={c: rename.get(c, c) for c in df.columns})

    need = [
        'account_name', 'ticker', 'description', 'quantity',
        'current_price_usd', 'current_value_usd',
        'cost_basis_total_usd', 'average_cost_basis_usd', 'type'
    ]
    for c in need:
        if c not in df.columns:
            df[c] = ''

    df['ticker'] = df['ticker'].fillna('').astype(str).str.strip().str.upper()
    df['account_name'] = df['account_name'].fillna('Fidelity Taxable').astype(str).str.strip()
    df['description'] = df['description'].fillna('').astype(str).str.strip()

    for c in ['quantity', 'current_price_usd', 'current_value_usd', 'cost_basis_total_usd', 'average_cost_basis_usd']:
        df[c] = df[c].apply(clean_num if c == 'quantity' else clean_money)

    df = df[df['ticker'] != ''].copy()
    df = df[~df['ticker'].str.startswith('DATE DOWNLOADED', na=False)].copy()
    df = df[~df['ticker'].str.startswith('BROKERAGE SERVICES ARE PROVIDED', na=False)].copy()
    df = df[~df['ticker'].str.startswith('THE DATA AND INFORMATION', na=False)].copy()
    df = df[~df['ticker'].isin(['SPAXX', 'SPAXX**'])].copy()
    return df


def normalize_buy_lots(df):
    out = df.copy()
    out.columns = [c.strip() for c in out.columns]

    rename = {
        'symbol': 'symbol',
        'ticker': 'symbol',
        'cusip': 'cusip',
        'acquisition_date': 'acquisition_date',
        'lot_quantity': 'lot_quantity',
        'quantity': 'lot_quantity',
        'unit_price_usd': 'unit_price_usd',
        'price': 'unit_price_usd',
        'price_usd': 'unit_price_usd',
        'lot_cost_basis_usd': 'lot_cost_basis_usd',
        'cost_basis_usd': 'lot_cost_basis_usd'
    }
    out = out.rename(columns={c: rename.get(c, c) for c in out.columns})

    required = ['symbol', 'cusip', 'acquisition_date', 'lot_quantity', 'unit_price_usd', 'lot_cost_basis_usd']
    for c in required:
        if c not in out.columns:
            out[c] = ''

    out['symbol'] = out['symbol'].fillna('').astype(str).str.strip().str.upper()
    out['cusip'] = out['cusip'].fillna('').astype(str).str.strip()
    out['acquisition_date'] = pd.to_datetime(out['acquisition_date']).dt.date
    out['lot_quantity'] = out['lot_quantity'].apply(clean_num)
    out['unit_price_usd'] = out['unit_price_usd'].apply(clean_money)
    out['lot_cost_basis_usd'] = out['lot_cost_basis_usd'].apply(clean_money)

    missing_basis = out['lot_cost_basis_usd'].abs() < 1e-12
    out.loc[missing_basis, 'lot_cost_basis_usd'] = (
        out.loc[missing_basis, 'lot_quantity'] * out.loc[missing_basis, 'unit_price_usd']
    )

    out = out[out['symbol'] != ''].copy()
    out = out[out['lot_quantity'] > 1e-12].copy()
    return out


def normalize_sells(df):
    out = df.copy()
    out.columns = [c.strip() for c in out.columns]

    rename = {
        'symbol': 'symbol',
        'ticker': 'symbol',
        'cusip': 'cusip',
        'sell_date': 'sell_date',
        'date': 'sell_date',
        'activity_date': 'sell_date',
        'sell_quantity': 'sell_quantity',
        'quantity': 'sell_quantity',
        'sell_price_usd': 'sell_price_usd',
        'price': 'sell_price_usd',
        'price_usd': 'sell_price_usd'
    }
    out = out.rename(columns={c: rename.get(c, c) for c in out.columns})

    required = ['symbol', 'cusip', 'sell_date', 'sell_quantity', 'sell_price_usd']
    for c in required:
        if c not in out.columns:
            out[c] = ''

    out['symbol'] = out['symbol'].fillna('').astype(str).str.strip().str.upper()
    out['cusip'] = out['cusip'].fillna('').astype(str).str.strip()
    out['sell_date'] = pd.to_datetime(out['sell_date']).dt.date
    out['sell_quantity'] = out['sell_quantity'].apply(clean_num)
    out['sell_price_usd'] = out['sell_price_usd'].apply(clean_money)

    out = out[out['symbol'] != ''].copy()
    out = out[out['sell_quantity'] > 1e-12].copy()
    return out


def load_split_events_from_raw_history(csv_file):
    raw = pd.read_csv(csv_file)
    raw.columns = [c.strip() for c in raw.columns]

    rename = {
        'Activity Date': 'activity_date',
        'Instrument': 'symbol',
        'Description': 'description',
        'Trans Code': 'trans_code',
        'Quantity': 'quantity',
        'Price': 'price',
        'Amount': 'amount'
    }
    raw = raw.rename(columns={c: rename.get(c, c) for c in raw.columns})

    needed = ['activity_date', 'symbol', 'description', 'trans_code', 'quantity']
    for c in needed:
        if c not in raw.columns:
            raw[c] = ''

    raw['symbol'] = raw['symbol'].fillna('').astype(str).str.strip().str.upper()
    raw['description'] = raw['description'].fillna('').astype(str).str.strip()
    raw['trans_code'] = raw['trans_code'].fillna('').astype(str).str.strip().str.upper()
    raw['activity_date'] = pd.to_datetime(raw['activity_date'], errors='coerce').dt.date
    raw['quantity'] = raw['quantity'].apply(clean_num)

    split_mask = (
        raw['trans_code'].str.startswith('SPL', na=False) |
        raw['description'].str.contains(r'\bSPL\b', case=False, na=False)
    )

    splits = raw.loc[split_mask, ['activity_date', 'symbol', 'description', 'trans_code', 'quantity']].copy()
    splits = splits[splits['symbol'] != ''].copy()
    splits = splits[splits['activity_date'].notna()].copy()
    splits = splits[splits['quantity'].abs() > 1e-12].copy()

    splits = splits.rename(columns={
        'activity_date': 'event_date',
        'quantity': 'split_additional_qty'
    })

    splits = splits.sort_values(['symbol', 'event_date']).reset_index(drop=True)
    return splits


def apply_split_to_open_lots(lots, split_additional_qty):
    open_qty = sum(float(l['lot_quantity']) for l in lots if float(l['lot_quantity']) > 1e-12)
    if open_qty <= 1e-12 or split_additional_qty <= 1e-12:
        return lots

    active_idx = [i for i, l in enumerate(lots) if float(l['lot_quantity']) > 1e-12]
    distributed = 0.0

    for pos, idx in enumerate(active_idx):
        lot = lots[idx]
        old_qty = float(lot['lot_quantity'])
        total_cost = float(lot.get('lot_cost_basis_usd', old_qty * float(lot['unit_price_usd'])))

        if pos < len(active_idx) - 1:
            add_qty = split_additional_qty * (old_qty / open_qty)
            add_qty = round(add_qty, 12)
            distributed += add_qty
        else:
            add_qty = split_additional_qty - distributed

        new_qty = old_qty + add_qty
        if new_qty <= 1e-12:
            continue

        lot['lot_quantity'] = new_qty
        lot['lot_cost_basis_usd'] = total_cost
        lot['unit_price_usd'] = total_cost / new_qty

    return lots


def fifo_remaining_lots(buys, sells, splits):
    remaining_rows = []
    recon_rows = []

    all_symbols = sorted(set(buys['symbol']).union(set(sells['symbol'])).union(set(splits['symbol'])))

    for ticker in all_symbols:
        buy_grp = buys[buys['symbol'] == ticker].sort_values(['acquisition_date', 'lot_quantity']).copy()
        sell_grp = sells[sells['symbol'] == ticker].sort_values(['sell_date', 'sell_quantity']).copy()
        split_grp = splits[splits['symbol'] == ticker].sort_values(['event_date', 'split_additional_qty']).copy()

        lots = buy_grp.to_dict('records')

        events = []
        for _, r in split_grp.iterrows():
            events.append({
                'event_type': 'SPLIT',
                'event_date': r['event_date'],
                'qty': float(r['split_additional_qty'])
            })
        for _, r in sell_grp.iterrows():
            events.append({
                'event_type': 'SELL',
                'event_date': r['sell_date'],
                'qty': float(r['sell_quantity'])
            })

        events = sorted(events, key=lambda x: (x['event_date'], 0 if x['event_type'] == 'SPLIT' else 1))

        for e in events:
            if e['event_type'] == 'SPLIT':
                lots = apply_split_to_open_lots(lots, e['qty'])
            else:
                qty = e['qty']
                i = 0
                while qty > 1e-9 and i < len(lots):
                    avail = float(lots[i]['lot_quantity'])
                    if avail <= 1e-9:
                        i += 1
                        continue
                    take = min(avail, qty)
                    lots[i]['lot_quantity'] = avail - take
                    lots[i]['lot_cost_basis_usd'] = float(lots[i]['lot_quantity']) * float(lots[i]['unit_price_usd'])
                    qty -= take
                    if lots[i]['lot_quantity'] <= 1e-9:
                        i += 1

        historical_buy_qty = float(buy_grp['lot_quantity'].sum()) if not buy_grp.empty else 0.0
        historical_split_qty = float(split_grp['split_additional_qty'].sum()) if not split_grp.empty else 0.0
        historical_sell_qty = float(sell_grp['sell_quantity'].sum()) if not sell_grp.empty else 0.0
        historical_remaining_qty = 0.0

        for lot in lots:
            q = float(lot['lot_quantity'])
            if q > 1e-9:
                historical_remaining_qty += q
                remaining_rows.append({
                    'ticker': ticker,
                    'cusip': lot.get('cusip', ''),
                    'acquisition_date': lot['acquisition_date'],
                    'lot_quantity': round(q, 12),
                    'unit_price_usd': round(float(lot['unit_price_usd']), 12),
                    'lot_cost_basis_usd': round(float(lot['lot_cost_basis_usd']), 12),
                    'source': 'history_fifo'
                })

        recon_rows.append({
            'ticker': ticker,
            'historical_buy_qty': round(historical_buy_qty, 12),
            'historical_split_qty': round(historical_split_qty, 12),
            'historical_sell_qty': round(historical_sell_qty, 12),
            'historical_remaining_qty': round(historical_remaining_qty, 12)
        })

    return pd.DataFrame(remaining_rows), pd.DataFrame(recon_rows)


def main():
    raw_positions = load_current_positions(RAW_POSITIONS)
    print(f'RAW_POSITIONS = {RAW_POSITIONS}')
    print(f'raw rows = {len(raw_positions)}')
    if {'Symbol', 'Quantity'}.issubset(set(raw_positions.columns)):
        print(raw_positions[['Symbol', 'Quantity']].to_string(index=False))

    positions = normalize_positions(raw_positions)
    print(f'normalized rows = {len(positions)}')
    print('tickers =', sorted(positions['ticker'].tolist()))

    buys = normalize_buy_lots(pd.read_csv(BUY_LOTS_FILE))
    sells = normalize_sells(pd.read_csv(SELLS_FILE))
    splits = load_split_events_from_raw_history(RAW_HISTORY)

    print(f'RAW_HISTORY = {RAW_HISTORY}')
    print(f'split rows = {len(splits)}')
    if not splits.empty:
        print(splits[['symbol', 'event_date', 'split_additional_qty', 'trans_code']].to_string(index=False))

    history_lots, recon = fifo_remaining_lots(buys, sells, splits)

    pos_map = positions[['ticker', 'quantity', 'cost_basis_total_usd', 'average_cost_basis_usd']].copy()
    pos_map = pos_map.rename(columns={
        'quantity': 'fidelity_qty',
        'cost_basis_total_usd': 'fidelity_cost_basis_total_usd',
        'average_cost_basis_usd': 'fidelity_avg_cost_basis_usd'
    })

    recon = recon.merge(pos_map, how='outer', left_on='ticker', right_on='ticker')
    for c in [
        'historical_buy_qty', 'historical_split_qty', 'historical_sell_qty',
        'historical_remaining_qty', 'fidelity_qty',
        'fidelity_cost_basis_total_usd', 'fidelity_avg_cost_basis_usd'
    ]:
        if c not in recon.columns:
            recon[c] = 0.0
        recon[c] = recon[c].fillna(0.0)

    recon['qty_diff'] = recon['fidelity_qty'] - recon['historical_remaining_qty']
    recon['scale_factor'] = recon.apply(
        lambda r: (r['fidelity_qty'] / r['historical_remaining_qty'])
        if abs(r['historical_remaining_qty']) > 1e-9 else 0.0,
        axis=1
    )

    note = []
    final_rows = []

    hist_by_ticker = {t: g.copy() for t, g in history_lots.groupby('ticker')} if not history_lots.empty else {}

    for _, r in recon.sort_values('ticker').iterrows():
        ticker = r['ticker']
        fidelity_qty = float(r['fidelity_qty'])
        hist_qty = float(r['historical_remaining_qty'])

        if fidelity_qty <= 1e-9:
            note.append('NO_CURRENT_POSITION')
            continue

        if hist_qty <= 1e-9:
            note.append('NO_HISTORY_MATCH_SINGLE_SYNTHETIC_LOT')
            final_rows.append({
                'account': 'Fidelity Taxable',
                'ticker': ticker,
                'acquisition_date': '',
                'open_quantity': round(fidelity_qty, 12),
                'unit_cost_usd': round(float(r['fidelity_avg_cost_basis_usd']), 12),
                'total_cost_basis_usd': round(float(r['fidelity_cost_basis_total_usd']), 12),
                'source': 'synthetic_from_fidelity'
            })
            continue

        if abs(fidelity_qty - hist_qty) <= 1e-6:
            note.append('MATCHED_EXACT_HISTORY')
            g = hist_by_ticker[ticker]
            for _, lot in g.iterrows():
                final_rows.append({
                    'account': 'Fidelity Taxable',
                    'ticker': ticker,
                    'acquisition_date': lot['acquisition_date'],
                    'open_quantity': round(float(lot['lot_quantity']), 12),
                    'unit_cost_usd': round(float(lot['unit_price_usd']), 12),
                    'total_cost_basis_usd': round(float(lot['lot_cost_basis_usd']), 12),
                    'source': lot['source']
                })
            continue

        note.append('SCALED_HISTORY_TO_FIDELITY_QUANTITY')
        scale = fidelity_qty / hist_qty
        g = hist_by_ticker[ticker].copy()
        running_qty = 0.0
        running_cost = 0.0

        rows = g.to_dict('records')
        for i, lot in enumerate(rows):
            old_qty = float(lot['lot_quantity'])
            if i < len(rows) - 1:
                new_qty = round(old_qty * scale, 12)
            else:
                new_qty = round(fidelity_qty - running_qty, 12)

            unit_cost = float(lot['unit_price_usd'])
            total_cost = round(new_qty * unit_cost, 12)

            running_qty += new_qty
            running_cost += total_cost

            final_rows.append({
                'account': 'Fidelity Taxable',
                'ticker': ticker,
                'acquisition_date': lot['acquisition_date'],
                'open_quantity': new_qty,
                'unit_cost_usd': round(unit_cost, 12),
                'total_cost_basis_usd': total_cost,
                'source': 'scaled_history_fifo'
            })

    recon['reconciliation_note'] = note

    final_df = pd.DataFrame(final_rows).sort_values(['ticker', 'acquisition_date'], na_position='last')
    final_df.to_csv(OUT_FILE, index=False)
    recon.to_csv(RECON_FILE, index=False)

    print(f'Wrote {OUT_FILE} and {RECON_FILE}')


if __name__ == '__main__':
    main()