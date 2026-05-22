# ============================================================
# engine/absorption.py
# Deteksi Absorption dari raw trade data
# Konsep: volume besar tapi harga tidak bergerak = absorption
# ============================================================

import time
from loguru import logger
from data.binance_ws import (get_recent_trades, get_buy_sell_volume,
                              get_big_orders, current_price)
from config import OF_CONFIG


def calculate_absorption_score(seconds: int = 60) -> dict:
    """
    Hitung absorption score dari data real-time.

    Absorption BULLISH:
    → Banyak seller agresif masuk (sell market orders besar)
    → Tapi harga TIDAK turun (ada buyer pasif yang menyerap)
    → Signal: potential long

    Absorption BEARISH:
    → Banyak buyer agresif masuk (buy market orders besar)
    → Tapi harga TIDAK naik (ada seller pasif yang menyerap)
    → Signal: potential short
    """
    trades    = get_recent_trades(seconds)
    if len(trades) < 10:
        return {"score": 0, "type": "NONE", "confidence": 0, "is_bullish": False, "is_bearish": False}

    # Split buy vs sell
    buy_trades  = [t for t in trades if not t["is_sell"]]
    sell_trades = [t for t in trades if t["is_sell"]]

    buy_vol  = sum(t["qty"] for t in buy_trades)
    sell_vol = sum(t["qty"] for t in sell_trades)

    # Hitung pergerakan harga
    if len(trades) >= 2:
        price_start = trades[0]["price"]
        price_end   = trades[-1]["price"]
        price_move  = abs(price_end - price_start)
        price_move_pct = price_move / price_start * 100
    else:
        price_move_pct = 0

    # Average volume per trade
    avg_trade_vol = (buy_vol + sell_vol) / len(trades)
    vol_mult      = (buy_vol + sell_vol) / max(avg_trade_vol * len(trades), 1)

    # Absorption score logic
    score = 0
    abs_type  = "NONE"
    confidence = 0

    # Volume besar = syarat pertama
    is_high_volume = (buy_vol + sell_vol) > avg_trade_vol * OF_CONFIG["absorption_vol_mult"]

    # Harga tidak bergerak = syarat kedua
    is_small_move = price_move_pct < 0.1  # kurang dari 0.1%

    if is_high_volume and is_small_move:
        if sell_vol > buy_vol * 1.5:
            # Seller dominan tapi harga tidak turun = BULLISH absorption
            abs_type = "BULLISH"
            score    = min(10, int(sell_vol / buy_vol * 3))
        elif buy_vol > sell_vol * 1.5:
            # Buyer dominan tapi harga tidak naik = BEARISH absorption
            abs_type = "BEARISH"
            score    = min(10, int(buy_vol / sell_vol * 3))

    confidence = min(100, score * 10)

    result = {
        "score"      : score,
        "type"       : abs_type,
        "confidence" : confidence,
        "buy_vol"    : buy_vol,
        "sell_vol"   : sell_vol,
        "price_move" : price_move_pct,
        "is_bullish" : abs_type == "BULLISH",
        "is_bearish" : abs_type == "BEARISH",
    }

    if score >= 5:
        logger.info(f"🔵 ABSORPTION {abs_type} detected | "
                    f"Score: {score}/10 | "
                    f"Buy: {buy_vol:.3f} | Sell: {sell_vol:.3f} | "
                    f"Move: {price_move_pct:.3f}%")

    return result


def detect_big_order_absorption(price_level: float,
                                tolerance_pct: float = 0.001,
                                seconds: int = 30) -> dict:
    """
    Deteksi apakah big orders sedang diserap di level tertentu.
    Ini versi lebih presisi dari Fabio's bubble detection.

    price_level  : level yang sedang kita perhatikan
    tolerance_pct: seberapa dekat dengan level
    """
    trades = get_recent_trades(seconds)
    if not trades:
        return {"absorbed": False, "strength": 0}

    # Filter trades di dekat level
    tolerance    = price_level * tolerance_pct
    near_trades  = [t for t in trades
                    if abs(t["price"] - price_level) <= tolerance]

    if not near_trades:
        return {"absorbed": False, "strength": 0}

    # Cek big orders di level ini
    big_threshold = OF_CONFIG["min_bubble_contracts"] * price_level * 0.001
    big_orders    = [t for t in near_trades if t["usd_val"] >= big_threshold]

    if not big_orders:
        return {"absorbed": False, "strength": 0}

    buy_big  = [t for t in big_orders if not t["is_sell"]]
    sell_big = [t for t in big_orders if t["is_sell"]]

    # Hitung pergerakan harga setelah big order
    if len(trades) >= 2:
        price_after = trades[-1]["price"]
        price_move  = (price_after - price_level) / price_level * 100
    else:
        price_move = 0

    # Absorption: big sell orders tapi harga tidak turun
    bull_absorbed = (
        len(sell_big) > len(buy_big) and
        price_move >= -0.05  # harga tidak turun signifikan
    )
    # Absorption: big buy orders tapi harga tidak naik
    bear_absorbed = (
        len(buy_big) > len(sell_big) and
        price_move <= 0.05   # harga tidak naik signifikan
    )

    strength = len(big_orders) * 2  # semakin banyak big order = lebih kuat

    return {
        "absorbed"      : bull_absorbed or bear_absorbed,
        "type"          : "BULLISH" if bull_absorbed else "BEARISH" if bear_absorbed else "NONE",
        "strength"      : min(strength, 10),
        "big_buy_count" : len(buy_big),
        "big_sell_count": len(sell_big),
        "price_move"    : price_move,
    }


def detect_iceberg(price_level: float,
                   order_book: dict,
                   tolerance_pct: float = 0.001) -> bool:
    """
    Deteksi iceberg order: level yang tidak habis-habis meski dipukul.
    Ini konfirmasi absorption yang LEBIH kuat.
    """
    tolerance = price_level * tolerance_pct

    # Lihat apakah ada order besar di level ini
    bids = order_book.get("bids", {})
    asks = order_book.get("asks", {})

    for price, qty in bids.items():
        if abs(price - price_level) <= tolerance and qty > 50:
            return True  # ada order besar di bid

    for price, qty in asks.items():
        if abs(price - price_level) <= tolerance and qty > 50:
            return True  # ada order besar di ask

    return False
