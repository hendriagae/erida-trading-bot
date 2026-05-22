# ============================================================
# test_connection.py
# Test semua koneksi sebelum mulai trading
# Jalankan ini PERTAMA KALI setelah setup
# ============================================================

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from dotenv import load_dotenv
load_dotenv()

import requests
from colorama import Fore, Style, init
init(autoreset=True)

OK  = f"{Fore.GREEN}✅{Style.RESET_ALL}"
ERR = f"{Fore.RED}❌{Style.RESET_ALL}"
WRN = f"{Fore.YELLOW}⚠️{Style.RESET_ALL}"

print(f"\n{'='*55}")
print(f"  🤖  AOS TRADING BOT — CONNECTION TEST")
print(f"{'='*55}\n")

all_ok = True

# ── TEST 1: Binance API ──────────────────────────────────────
print("1️⃣  Testing Binance Futures API...")
try:
    from binance.client import Client
    from config import BINANCE_API_KEY, BINANCE_SECRET_KEY, TESTNET, SYMBOL
    client = Client(BINANCE_API_KEY, BINANCE_SECRET_KEY, testnet=TESTNET)

    balances = client.futures_account_balance()
    usdt_bal = next(
        (float(b.get("balance", 0)) for b in balances if b["asset"] == "USDT"),
        0
    )
    ticker = client.futures_symbol_ticker(symbol=SYMBOL)
    price  = float(ticker["price"])

    mode = "TESTNET" if TESTNET else "LIVE"
    print(f"   {OK} Binance {mode}: Connected")
    print(f"   {OK} USDT Balance: ${usdt_bal:,.2f}")
    print(f"   {OK} BTC Price: ${price:,.2f}")

except Exception as e:
    print(f"   {ERR} Binance Error: {e}")
    print(f"   {WRN} Cek API key di file .env")
    all_ok = False

# ── TEST 2: Telegram ─────────────────────────────────────────
print("\n2️⃣  Testing Telegram...")
try:
    from config import TELEGRAM_CONFIG
    token   = TELEGRAM_CONFIG["bot_token"]
    chat_id = TELEGRAM_CONFIG["chat_id"]

    if not token or token == "isi_token_telegram_kamu_disini":
        print(f"   {WRN} Telegram belum dikonfigurasi di .env")
    else:
        url  = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(url, data={
            "chat_id"    : chat_id,
            "text"       : "🤖 AOS Bot: Test koneksi berhasil! Bot siap jalan.",
            "parse_mode" : "HTML",
        }, timeout=10)
        if resp.status_code == 200:
            print(f"   {OK} Telegram: Connected — cek HP kamu!")
        else:
            print(f"   {ERR} Telegram error: {resp.text}")
            all_ok = False

except Exception as e:
    print(f"   {ERR} Telegram Error: {e}")
    all_ok = False

# ── TEST 3: yfinance (Macro Data) ────────────────────────────
print("\n3️⃣  Testing Macro Data (yfinance)...")
try:
    import yfinance as yf
    import warnings
    warnings.filterwarnings("ignore")

    ok_count = 0
    tickers  = {"DXY": "DX-Y.NYB", "SPX": "^GSPC"}

    for name, ticker in tickers.items():
        try:
            data = yf.download(ticker, period="5d",
                               interval="1d", progress=False,
                               auto_adjust=True)
            if data is not None and len(data) > 0:
                if hasattr(data.columns, 'levels'):
                    close = data['Close'].squeeze()
                else:
                    close = data['Close']
                price = float(close.iloc[-1])
                print(f"   {OK} {name}: {price:,.2f}")
                ok_count += 1
            else:
                print(f"   {WRN} {name}: No data (market tutup / weekend)")
        except Exception:
            print(f"   {WRN} {name}: Gagal — mungkin market tutup")

    if ok_count == 0:
        print(f"   {WRN} Semua macro data gagal")
        print(f"      Ini NORMAL saat weekend atau market tutup.")
        print(f"      Bot tetap jalan menggunakan Fear&Greed saja.")

except Exception as e:
    print(f"   {WRN} yfinance Error: {e}")
    print(f"      Bot tetap bisa jalan tanpa data ini.")

# ── TEST 4: Fear & Greed ─────────────────────────────────────
print("\n4️⃣  Testing Fear & Greed Index...")
try:
    resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
    data = resp.json()["data"][0]
    print(f"   {OK} Fear & Greed: {data['value']} ({data['value_classification']})")
except Exception as e:
    print(f"   {ERR} Fear & Greed Error: {e}")

# ── TEST 5: Database ─────────────────────────────────────────
print("\n5️⃣  Testing Database...")
try:
    from data.database import init_database
    init_database()
    print(f"   {OK} Database: Initialized (logs/trading_bot.db)")
except Exception as e:
    print(f"   {ERR} Database Error: {e}")
    all_ok = False

# ── TEST 6: Deribit (Options GEX) ───────────────────────────
print("\n6️⃣  Testing Deribit (Options GEX)...")
try:
    resp = requests.get(
        "https://www.deribit.com/api/v2/public/get_book_summary_by_currency",
        params  = {"currency": "BTC", "kind": "option"},
        timeout = 10,
        verify  = False,   # Fix SSL cert issue di Windows
    )
    data = resp.json().get("result", [])
    print(f"   {OK} Deribit: {len(data)} BTC options instruments loaded")
except Exception as e:
    print(f"   {WRN} Deribit: Gagal — tidak kritis, bot tetap jalan")
    print(f"      ({e})")

# ── RESULT ───────────────────────────────────────────────────
print(f"\n{'='*55}")
if all_ok:
    print(f"  {OK}  ALL CRITICAL TESTS PASSED")
    print(f"       Bot siap dijalankan!")
    print(f"\n  ℹ️  Note: yfinance dan Deribit adalah data OPTIONAL.")
    print(f"       Bot tetap berfungsi penuh tanpanya.")
else:
    print(f"  {ERR}  ADA TEST KRITIS YANG GAGAL")
    print(f"       Perbaiki dulu sebelum jalankan bot.")
print(f"{'='*55}\n")
