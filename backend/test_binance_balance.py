#!/usr/bin/env python3
"""Test script to call Binance fapi.v2.account and show real balance fields."""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parents[1] / '.env'
load_dotenv(env_path, override=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

def redact_key(k: str) -> str:
    if not k:
        return "<empty>"
    if len(k) < 8:
        return "***"
    return k[:4] + "****" + k[-4:]

print("=" * 60)
print("BINANCE FUTURES BALANCE DEBUG")
print("=" * 60)
print(f"API_KEY:      {redact_key(os.getenv('BINANCE_API_KEY',''))}")
print(f"API_SECRET:   {redact_key(os.getenv('BINANCE_API_SECRET',''))}")
print(f"TESTNET:      {os.getenv('BINANCE_TESTNET','false')}")
print(f"LEVERAGE:     {os.getenv('BINANCE_LEVERAGE','10')}")
print(f"TRADE_USDT:   {os.getenv('TRADE_USDT_AMOUNT','10')}")
print(f"MARGIN_TYPE:  {os.getenv('BINANCE_MARGIN_TYPE','ISOLATED')}")

from binance.client import Client

try:
    client = Client(
        api_key=os.getenv('BINANCE_API_KEY',''),
        api_secret=os.getenv('BINANCE_API_SECRET',''),
        testnet=os.getenv('BINANCE_TESTNET','false').lower() == 'true',
    )

    acct = client.futures_account()

    print()
    print("--- futures_account() root fields ---")
    for key in sorted(acct.keys()):
        print(f"  {key}: {acct[key]}")

    # Numeric fields we care about
    print()
    total_wallet   = float(acct.get('totalWalletBalance', 0))
    available      = float(acct.get('availableBalance', 0))
    margin_bal     = float(acct.get('totalMarginBalance', total_wallet))
    unrealized     = float(acct.get('totalUnrealizedProfit', 0))
    initial_margin = float(acct.get('totalInitialMargin', 0))
    maint_margin   = float(acct.get('totalMaintMargin', 0))

    print(f"totalWalletBalance   = {total_wallet:>12.4f}")
    print(f"availableBalance     = {available:>12.4f}")
    print(f"totalMarginBalance   = {margin_bal:>12.4f}")
    print(f"totalUnrealizedProfit= {unrealized:>12.4f}")
    print(f"totalInitialMargin   = {initial_margin:>12.4f}")
    print(f"totalMaintMargin     = {maint_margin:>12.4f}")
    print(f"  => calculated_free= {margin_bal - initial_margin:>12.4f}")

    # Positions
    print()
    print("--- OPEN POSITIONS ---")
    positions = [p for p in client.futures_position_information() if abs(float(p.get('positionAmt', 0))) > 0]
    if not positions:
        print("  (none)")
    for p in positions:
        amt = float(p['positionAmt'])
        print(f"  {p['symbol']:12} side={'LONG' if amt>0 else 'SHORT':5}  amt={abs(amt):>10.4f}  entry={float(p['entryPrice']):>10.4f}  unrealized={float(p['unRealizedProfit']):>10.4f}  marginType={p.get('marginType','?')}  leverage={p.get('leverage','?')}")

    # Open orders
    print()
    print("--- OPEN ORDERS ---")
    open_orders = client.futures_get_open_orders()
    if not open_orders:
        print("  (none)")
    for o in open_orders:
        print(f"  {o['symbol']:12} {o['side']:4} {o['type']:16} qty={o['origQty']} price={o.get('price',0)}")

    # account balance per asset (USDT, USDC, etc)
    print()
    print("--- ASSET BALANCES ---")
    for asset_b in client.futures_account_balance():
        asset = asset_b['asset']
        bal   = float(asset_b.get('balance', 0))
        cross = float(asset_b.get('crossWalletBalance', 0))
        avail = float(asset_b.get('availableBalance', 0))
        if bal != 0 or cross != 0 or avail != 0:
            print(f"  {asset:>8}: balance={bal:>12.4f}  crossWallet={cross:>12.4f}  available={avail:>12.4f}")

except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()

print("=" * 60)
