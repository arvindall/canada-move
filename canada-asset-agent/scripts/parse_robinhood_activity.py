from pathlib import Path
from io import StringIO
import pandas as pd
import re

BASE = Path(__file__).resolve().parents[1]
RAW_FILE = BASE / 'data' / 'raw' / 'brokerage' / 'robinhood_activity_full_account.csv'
OUT_DIR = BASE / 'data' / 'parsed'
OUT_DIR.mkdir(parents=True, exist_ok=True)

BUY_CODES = {'Buy'}
SELL_CODES = {'Sell'}
EXCLUDE_CODES = {'ACH', 'INT', 'GOLD', 'GDBP', 'SLIP', 'DTAX', 'CDIV', 'MDIV', 'ITRF', 'FUTSWP', 'MINT', 'AFEE', 'DFEE'}


def money_to_float(v):
    if pd.isna(v) or str(v).strip() == '':
        return 0.0
    s = str(v).strip().replace('$', '').replace(',', '')
    if s.startswith('(') and s.endswith(')'):
        s = '-' + s[1:-1]
    return float(s)


def qty_to_float(v):
    if pd.isna(v) or str(v).strip() == '':
        return 0.0
    s = str(v).strip().replace(',', '')
    s = s.rstrip('S')
    return float(s)


def clean_symbol(v):
    if pd.isna(v):
        return ''
    return str(v).strip().upper()


def extract_cusip(desc):
    if pd.isna(desc):
        return ''
    m = re.search(r'CUSIP[: ]+([A-Z0-9]+)', str(desc))
    return m.group(1) if m else ''


def is_option_like(desc, symbol):
    text = f"{symbol} {desc}".upper()
    return (
        (' PUT ' in f' {text} ')
        or (' CALL ' in f' {text} ')
        or any(k in text for k in [' BTO', ' STO', ' BTC', ' STC'])
    )


def is_disclaimer_line(s):
    s2 = s.strip()
    return (
        s2.startswith('The data provided is for informational purposes only')
        or s2.startswith('Reminder:')
        or s2.startswith('Reminder ')
    )


def is_separator_line(s):
    s2 = s.strip().replace(' ', '')
    return s2 != '' and set(s2) == {'-'}


def load_robinhood_csv(raw_file):
    with open(raw_file, 'r', encoding='utf-8', errors='replace') as f:
        lines = f.readlines()

    cleaned = []
    header_seen = False

    for line in lines:
        s = line.strip()

        if not s:
            continue
        if is_disclaimer_line(s):
            continue
        if is_separator_line(s):
            continue

        if s.startswith('Activity Date'):
            if header_seen:
                continue
            header_seen = True

        cleaned.append(line)

    if not cleaned:
        raise ValueError(f'No parseable rows found in {raw_file}')

    df = pd.read_csv(
        StringIO(''.join(cleaned)),
        engine='python',
        sep=',',
        quotechar='"',
        on_bad_lines='warn'
    )
    return df


def main():
    if not RAW_FILE.exists():
        raise FileNotFoundError(f'Missing file: {RAW_FILE}')

    df = load_robinhood_csv(RAW_FILE)
    df.columns = [c.strip() for c in df.columns]

    req = ['Activity Date', 'Instrument', 'Description', 'Trans Code', 'Quantity', 'Price', 'Amount']
    missing = [c for c in req if c not in df.columns]
    if missing:
        raise ValueError(f'Missing required columns: {missing}')

    df['activity_date'] = pd.to_datetime(df['Activity Date'], errors='coerce')
    df['symbol'] = df['Instrument'].apply(clean_symbol)
    df['description'] = df['Description'].fillna('').astype(str)
    df['trans_code'] = df['Trans Code'].fillna('').astype(str).str.strip()
    df['quantity'] = df['Quantity'].apply(qty_to_float)
    df['price'] = df['Price'].apply(money_to_float)
    df['amount'] = df['Amount'].apply(money_to_float)
    df['cusip'] = df['description'].apply(extract_cusip)
    df['is_option_like'] = df.apply(lambda r: is_option_like(r['description'], r['symbol']), axis=1)
    df['is_asset_row'] = (
        (df['symbol'] != '')
        & (~df['trans_code'].isin(EXCLUDE_CODES))
        & (~df['is_option_like'])
    )

    normalized = df[
        ['activity_date', 'symbol', 'cusip', 'description', 'trans_code',
         'quantity', 'price', 'amount', 'is_option_like', 'is_asset_row']
    ].copy()
    normalized = normalized.sort_values(['activity_date', 'symbol', 'trans_code'])
    normalized['activity_date'] = normalized['activity_date'].dt.strftime('%Y-%m-%d')
    normalized.to_csv(OUT_DIR / 'robinhood_activity_normalized.csv', index=False)

    buys = df[
        df['is_asset_row']
        & df['trans_code'].isin(BUY_CODES)
        & (df['quantity'] > 0)
    ].copy()
    buys['acquisition_date'] = buys['activity_date'].dt.strftime('%Y-%m-%d')
    buys['lot_quantity'] = buys['quantity']
    buys['unit_price_usd'] = buys['price']
    buys['lot_cost_basis_usd'] = buys['lot_quantity'] * buys['unit_price_usd']
    buys['source'] = 'robinhood_history'

    buy_lots = buys[
        ['symbol', 'cusip', 'acquisition_date', 'lot_quantity',
         'unit_price_usd', 'lot_cost_basis_usd', 'source']
    ].copy()
    buy_lots.to_csv(OUT_DIR / 'robinhood_buy_lots.csv', index=False)

    sells = df[
        df['is_asset_row']
        & df['trans_code'].isin(SELL_CODES)
        & (df['quantity'] > 0)
    ].copy()
    sells['sell_date'] = sells['activity_date'].dt.strftime('%Y-%m-%d')

    sell_lots = sells[
        ['symbol', 'cusip', 'sell_date', 'quantity', 'price', 'amount']
    ].copy()
    sell_lots = sell_lots.rename(columns={
        'quantity': 'sell_quantity',
        'price': 'sell_price_usd',
        'amount': 'sell_amount_usd'
    })
    sell_lots.to_csv(OUT_DIR / 'robinhood_sell_activity.csv', index=False)

    print(f'Loaded {len(df)} cleaned rows from {RAW_FILE}')
    print('Wrote robinhood_activity_normalized.csv, robinhood_buy_lots.csv, robinhood_sell_activity.csv')


if __name__ == '__main__':
    main()
