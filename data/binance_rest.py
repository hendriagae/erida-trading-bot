# ============================================================
# data/binance_rest.py
# Ambil data dari Binance REST API
# (Funding rate, OI, balance, order management)
# ============================================================

from binance.client import Client
from binance.exceptions import BinanceAPIException
from loguru import logger
from config import BINANCE_API_KEY, BINANCE_SECRET_KEY, TESTNET, SYMBOL


def get_client() -> Client:
    """Buat Binance client."""
    return Client(
        BINANCE_API_KEY,
        BINANCE_SECRET_KEY,
        testnet=TESTNET,
    )


def get_account_balance() -> dict:
    """Ambil saldo Futures wallet."""
    try:
        client  = get_client()
        balances = client.futures_account_balance()
        result  = {}
        for b in balances:
            if float(b["balance"]) > 0:
                result[b["asset"]] = {
                    "balance"            : float(b["balance"]),
                    "available_balance": float(b.get("withdrawAvailable", b.get("availableBalance", 0))),
                }
        return result
    except BinanceAPIException as e:
        logger.error(f"Balance error: {e}")
        return {}


def get_usdt_balance() -> float:
    """Ambil saldo USDT saja."""
    balances = get_account_balance()
    return balances.get("USDT", {}).get("balance", 0.0)


def get_funding_rate(symbol: str = SYMBOL) -> float:
    """Ambil funding rate saat ini."""
    try:
        client = get_client()
        data   = client.futures_funding_rate(symbol=symbol, limit=1)
        rate   = float(data[-1]["fundingRate"]) if data else 0.0
        logger.debug(f"Funding rate {symbol}: {rate:.6f}")
        return rate
    except Exception as e:
        logger.error(f"Funding rate error: {e}")
        return 0.0


def get_open_interest(symbol: str = SYMBOL) -> float:
    """Ambil open interest saat ini."""
    try:
        client = get_client()
        data   = client.futures_open_interest(symbol=symbol)
        oi     = float(data["openInterest"])
        logger.debug(f"Open Interest {symbol}: {oi:,.0f}")
        return oi
    except Exception as e:
        logger.error(f"Open interest error: {e}")
        return 0.0


def get_long_short_ratio(symbol: str = SYMBOL,
                         period: str = "1h") -> dict:
    """Ambil rasio long/short akun."""
    try:
        client = get_client()
        data   = client.futures_top_longshort_account_ratio(
            symbol=symbol, period=period, limit=1
        )
        if data:
            return {
                "long_pct"  : float(data[0]["longAccount"]),
                "short_pct" : float(data[0]["shortAccount"]),
                "ratio"     : float(data[0]["longShortRatio"]),
            }
    except Exception as e:
        logger.error(f"Long/short ratio error: {e}")
    return {"long_pct": 0.5, "short_pct": 0.5, "ratio": 1.0}


def get_klines(symbol: str = SYMBOL,
               interval: str = "1m",
               limit: int = 200) -> list:
    """Ambil data OHLCV (candle)."""
    try:
        client = get_client()
        klines = client.futures_klines(
            symbol=symbol, interval=interval, limit=limit
        )
        result = []
        for k in klines:
            result.append({
                "open_time" : k[0],
                "open"      : float(k[1]),
                "high"      : float(k[2]),
                "low"       : float(k[3]),
                "close"     : float(k[4]),
                "volume"    : float(k[5]),
            })
        return result
    except Exception as e:
        logger.error(f"Klines error: {e}")
        return []


def get_ticker_price(symbol: str = SYMBOL) -> float:
    """Ambil harga terakhir."""
    try:
        client = get_client()
        ticker = client.futures_symbol_ticker(symbol=symbol)
        return float(ticker["price"])
    except Exception as e:
        logger.error(f"Ticker error: {e}")
        return 0.0


def get_open_positions(symbol: str = SYMBOL) -> list:
    """Ambil posisi yang sedang terbuka."""
    try:
        client    = get_client()
        positions = client.futures_position_information(symbol=symbol)
        open_pos  = [p for p in positions
                     if float(p["positionAmt"]) != 0]
        return open_pos
    except Exception as e:
        logger.error(f"Positions error: {e}")
        return []


def set_leverage(symbol: str = SYMBOL, leverage: int = 10) -> bool:
    """Set leverage untuk symbol."""
    try:
        client = get_client()
        client.futures_change_leverage(symbol=symbol, leverage=leverage)
        logger.info(f"Leverage set: {symbol} = {leverage}x")
        return True
    except Exception as e:
        logger.error(f"Set leverage error: {e}")
        return False


def set_margin_mode(symbol: str = SYMBOL,
                    mode: str = "ISOLATED") -> bool:
    """Set margin mode (ISOLATED atau CROSSED)."""
    try:
        client = get_client()
        client.futures_change_margin_type(symbol=symbol, marginType=mode)
        logger.info(f"Margin mode set: {symbol} = {mode}")
        return True
    except Exception as e:
        # Error jika mode sudah sama = tidak masalah
        if "No need to change" in str(e):
            return True
        logger.error(f"Set margin mode error: {e}")
        return False


def get_market_snapshot(symbol: str = SYMBOL) -> dict:
    """Ambil semua data market sekaligus."""
    return {
        "price"         : get_ticker_price(symbol),
        "funding_rate"  : get_funding_rate(symbol),
        "open_interest" : get_open_interest(symbol),
        "long_short"    : get_long_short_ratio(symbol),
        "balance_usdt"  : get_usdt_balance(),
    }
