# ============================================================
# execution/order_manager.py
# ERIDA Trading Bot
# Fix v4: Internal SL/TP monitoring untuk testnet
# ============================================================

import time
from binance.client import Client
from binance.exceptions import BinanceAPIException
from loguru import logger
from config import (BINANCE_API_KEY, BINANCE_SECRET_KEY,
                    TESTNET, SYMBOL, IS_PAPER)


def get_client() -> Client:
    return Client(
        BINANCE_API_KEY,
        BINANCE_SECRET_KEY,
        testnet=TESTNET,
    )


def place_market_order(direction: str,
                        quantity: float,
                        symbol: str = SYMBOL) -> dict:
    if IS_PAPER:
        logger.info(f"[PAPER] Market: {direction} {quantity:.3f}")
        return {"orderId": "PAPER_MKT", "status": "FILLED"}
    side = "BUY" if direction == "LONG" else "SELL"
    try:
        client = get_client()
        order  = client.futures_create_order(
            symbol=symbol, side=side,
            type="MARKET", quantity=quantity,
        )
        logger.success(f"✅ Market: {direction} {quantity:.3f}")
        return order
    except BinanceAPIException as e:
        logger.error(f"Market order error: {e}")
        return {}
    except Exception as e:
        logger.error(f"Market order unexpected: {e}")
        return {}


def place_sl_order(direction: str,
                    quantity: float,
                    sl_price: float,
                    symbol: str = SYMBOL) -> dict:
    """
    Pasang Stop Loss.
    Di testnet: SL dipantau secara internal oleh bot.
    Di mainnet: pakai STOP_MARKET order.
    """
    if IS_PAPER:
        logger.info(f"[PAPER] SL: {direction} @ ${sl_price:,.2f}")
        return {"orderId": "PAPER_SL", "status": "NEW",
                "internal": True}

    side = "SELL" if direction == "LONG" else "BUY"

    # Coba STOP_MARKET (mainnet)
    try:
        client = get_client()
        order  = client.futures_create_order(
            symbol      = symbol,
            side        = side,
            type        = "STOP_MARKET",
            stopPrice   = round(sl_price, 2),
            quantity    = quantity,
            reduceOnly  = True,
            timeInForce = "GTC",
        )
        logger.success(f"✅ SL order @ ${sl_price:,.2f}")
        return order
    except BinanceAPIException as e:
        if "-4120" in str(e) or "-1111" in str(e):
            # Testnet tidak support conditional orders
            # Gunakan internal monitoring
            logger.warning(
                f"⚠️ SL order tidak didukung di testnet. "
                f"Menggunakan internal SL monitoring @ "
                f"${sl_price:,.2f}"
            )
            return {
                "orderId"  : f"INTERNAL_SL_{int(time.time())}",
                "status"   : "MONITORING",
                "internal" : True,
                "sl_price" : sl_price,
                "direction": direction,
                "quantity" : quantity,
            }
        logger.error(f"SL error: {e}")
        return {}
    except Exception as e:
        logger.error(f"SL unexpected: {e}")
        return {}


def place_tp_order(direction: str,
                    quantity: float,
                    tp_price: float,
                    symbol: str = SYMBOL) -> dict:
    """
    Pasang Take Profit.
    Di testnet: TP dipantau secara internal oleh bot.
    Di mainnet: pakai TAKE_PROFIT_MARKET order.
    """
    if IS_PAPER:
        logger.info(f"[PAPER] TP: {direction} @ ${tp_price:,.2f}")
        return {"orderId": "PAPER_TP", "status": "NEW",
                "internal": True}

    side = "SELL" if direction == "LONG" else "BUY"

    # Coba TAKE_PROFIT_MARKET (mainnet)
    try:
        client = get_client()
        order  = client.futures_create_order(
            symbol      = symbol,
            side        = side,
            type        = "TAKE_PROFIT_MARKET",
            stopPrice   = round(tp_price, 2),
            quantity    = quantity,
            reduceOnly  = True,
            timeInForce = "GTC",
        )
        logger.success(f"✅ TP order @ ${tp_price:,.2f}")
        return order
    except BinanceAPIException as e:
        if "-4120" in str(e) or "-1111" in str(e):
            logger.warning(
                f"⚠️ TP order tidak didukung di testnet. "
                f"Menggunakan internal TP monitoring @ "
                f"${tp_price:,.2f}"
            )
            return {
                "orderId"  : f"INTERNAL_TP_{int(time.time())}",
                "status"   : "MONITORING",
                "internal" : True,
                "tp_price" : tp_price,
                "direction": direction,
                "quantity" : quantity,
            }
        logger.error(f"TP error: {e}")
        return {}
    except Exception as e:
        logger.error(f"TP unexpected: {e}")
        return {}


def cancel_all_orders(symbol: str = SYMBOL) -> bool:
    if IS_PAPER:
        return True
    try:
        client = get_client()
        client.futures_cancel_all_open_orders(symbol=symbol)
        logger.info(f"All orders cancelled: {symbol}")
        return True
    except BinanceAPIException as e:
        logger.error(f"Cancel all error: {e}")
        return False


def close_position(direction: str,
                    quantity: float,
                    symbol: str = SYMBOL) -> dict:
    if IS_PAPER:
        return {"status": "FILLED"}
    side = "SELL" if direction == "LONG" else "BUY"
    try:
        client = get_client()
        order  = client.futures_create_order(
            symbol     = symbol,
            side       = side,
            type       = "MARKET",
            quantity   = quantity,
            reduceOnly = True,
        )
        logger.success(
            f"✅ Position closed: {direction} {quantity:.3f}"
        )
        return order
    except BinanceAPIException as e:
        logger.error(f"Close error: {e}")
        return {}


def get_position_info(symbol: str = SYMBOL,
                       retries: int = 3) -> dict:
    """Ambil info posisi dengan retry 3x."""
    if IS_PAPER:
        return {}

    results = []
    for attempt in range(retries):
        try:
            client    = get_client()
            positions = client.futures_position_information(
                symbol=symbol
            )
            for p in positions:
                amt = float(p.get("positionAmt", 0))
                if amt != 0:
                    pnl = float(
                        p.get("unrealizedProfit",
                        p.get("unRealizedProfit",
                        p.get("unrealized_profit", 0)))
                    )
                    return {
                        "direction"  : "LONG" if amt > 0 else "SHORT",
                        "quantity"   : abs(amt),
                        "entry"      : float(p.get("entryPrice", 0)),
                        "pnl"        : pnl,
                        "leverage"   : int(p.get("leverage", 10)),
                        "mark_price" : float(p.get("markPrice", 0)),
                    }
            results.append(None)
        except BinanceAPIException as e:
            logger.warning(
                f"Position fetch {attempt+1}/{retries}: {e}"
            )
            results.append("ERROR")
        except Exception as e:
            logger.warning(
                f"Position fetch unexpected "
                f"{attempt+1}/{retries}: {e}"
            )
            results.append("ERROR")

        if attempt < retries - 1:
            time.sleep(2)

    none_count  = results.count(None)
    error_count = sum(1 for r in results if r == "ERROR")

    if none_count == retries:
        logger.debug("Position confirmed closed (3x verified)")
        return {}
    elif error_count > 0:
        logger.warning(f"Position uncertain ({error_count} errors)")
        return {"uncertain": True}
    return {}


def update_sl_order(old_order_id: str,
                     direction: str,
                     new_sl: float,
                     quantity: float,
                     symbol: str = SYMBOL) -> dict:
    if IS_PAPER:
        return {"orderId": "PAPER_SL_NEW"}
    try:
        client = get_client()
        client.futures_cancel_order(
            symbol=symbol, orderId=old_order_id
        )
    except Exception:
        pass
    return place_sl_order(direction, quantity, new_sl, symbol)


def set_leverage(symbol: str = SYMBOL,
                  leverage: int = 10) -> bool:
    try:
        client = get_client()
        client.futures_change_leverage(
            symbol=symbol, leverage=leverage
        )
        logger.info(f"Leverage: {symbol} = {leverage}x")
        return True
    except BinanceAPIException as e:
        if "No need to change" not in str(e):
            logger.error(f"Leverage error: {e}")
        return True


def set_margin_mode(symbol: str = SYMBOL,
                     mode: str = "ISOLATED") -> bool:
    try:
        client = get_client()
        client.futures_change_margin_type(
            symbol=symbol, marginType=mode
        )
        logger.info(f"Margin: {symbol} = {mode}")
        return True
    except BinanceAPIException as e:
        if "No need to change" in str(e):
            return True
        if "4048" in str(e):
            logger.warning(
                "Cannot change margin — position open."
            )
            return True
        logger.error(f"Margin error: {e}")
        return False
