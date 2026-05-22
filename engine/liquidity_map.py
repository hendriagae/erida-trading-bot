# ============================================================
# engine/liquidity_map.py — LAYER 2: LIQUIDITY MAP
# ERIDA Trading Bot
# Fix: f-string backslash error untuk Python 3.10
# ============================================================

from loguru import logger


def find_liquidity_pools(klines: list,
                          lookback: int = 50) -> dict:
    """
    Temukan swing high/low yang menjadi liquidity pools.
    Buy-side  = di atas swing highs (stop loss SHORT)
    Sell-side = di bawah swing lows (stop loss LONG)
    """
    if len(klines) < lookback:
        lookback = len(klines)

    recent = klines[-lookback:]
    highs  = [k["high"]  for k in recent]
    lows   = [k["low"]   for k in recent]
    closes = [k["close"] for k in recent]

    swing_highs = []
    swing_lows  = []

    for i in range(2, len(recent) - 2):
        if (highs[i] > highs[i-1] and highs[i] > highs[i-2] and
                highs[i] > highs[i+1] and highs[i] > highs[i+2]):
            swing_highs.append({
                "price"    : highs[i],
                "index"    : i,
                "strength" : _calc_swing_strength(highs, i, "high"),
                "type"     : "buy_side",
            })

        if (lows[i] < lows[i-1] and lows[i] < lows[i-2] and
                lows[i] < lows[i+1] and lows[i] < lows[i+2]):
            swing_lows.append({
                "price"    : lows[i],
                "index"    : i,
                "strength" : _calc_swing_strength(lows, i, "low"),
                "type"     : "sell_side",
            })

    current_price = closes[-1]

    buy_side = sorted(
        [s for s in swing_highs if s["price"] > current_price],
        key=lambda x: x["price"]
    )[:3]

    sell_side = sorted(
        [s for s in swing_lows if s["price"] < current_price],
        key=lambda x: x["price"],
        reverse=True
    )[:3]

    result = {
        "buy_side_liquidity"  : buy_side,
        "sell_side_liquidity" : sell_side,
        "nearest_buy_liq"     : buy_side[0]["price"]  if buy_side  else 0,
        "nearest_sell_liq"    : sell_side[0]["price"] if sell_side else 0,
        "current_price"       : current_price,
    }

    # Fix: hindari backslash di f-string (Python 3.10)
    if buy_side:
        buy_prices = [f"${s['price']:,.0f}" for s in buy_side]
        logger.debug(f"Buy-side liquidity: {buy_prices}")
    if sell_side:
        sell_prices = [f"${s['price']:,.0f}" for s in sell_side]
        logger.debug(f"Sell-side liquidity: {sell_prices}")

    return result


def detect_liquidity_sweep(klines: list,
                            liq_map: dict,
                            tolerance_pct: float = 0.001) -> dict:
    """
    Deteksi apakah baru terjadi LIQUIDITY SWEEP.
    Sweep = spike melewati swing high/low tapi langsung balik.
    Ini adalah setup entry terbaik!
    """
    if len(klines) < 5:
        return {"swept": False, "direction": None}

    latest  = klines[-1]
    current = latest["close"]

    # Cek buy-side sweep (swept highs = bearish setelahnya)
    buy_liqs   = liq_map.get("buy_side_liquidity", [])
    bull_sweep = False
    bull_level = 0

    for liq in buy_liqs:
        level = liq["price"]
        tol   = level * tolerance_pct
        if (latest["high"] > level + tol and
                latest["close"] < level - tol and
                latest["close"] < latest["open"]):
            bull_sweep = True
            bull_level = level
            break

    # Cek sell-side sweep (swept lows = bullish setelahnya)
    sell_liqs  = liq_map.get("sell_side_liquidity", [])
    bear_sweep = False
    bear_level = 0

    for liq in sell_liqs:
        level = liq["price"]
        tol   = level * tolerance_pct
        if (latest["low"] < level - tol and
                latest["close"] > level + tol and
                latest["close"] > latest["open"]):
            bear_sweep = True
            bear_level = level
            break

    if bull_sweep:
        logger.info(
            f"💧 BUY-SIDE SWEEP @ ${bull_level:,.2f} | "
            f"Price: ${current:,.2f} | "
            f"Direction after: BEARISH"
        )
        return {
            "swept"     : True,
            "direction" : "BEARISH",
            "level"     : bull_level,
            "type"      : "buy_side_swept",
        }

    if bear_sweep:
        logger.info(
            f"💧 SELL-SIDE SWEEP @ ${bear_level:,.2f} | "
            f"Price: ${current:,.2f} | "
            f"Direction after: BULLISH"
        )
        return {
            "swept"     : True,
            "direction" : "BULLISH",
            "level"     : bear_level,
            "type"      : "sell_side_swept",
        }

    return {"swept": False, "direction": None,
            "level": 0, "type": None}


def is_price_near_liquidity(price: float,
                              liq_map: dict,
                              tolerance_pct: float = 0.001) -> dict:
    """
    Cek apakah harga sedang mendekati liquidity pool.
    Jika ya = JANGAN ENTRY dulu, tunggu sweep!
    """
    nearest_buy  = liq_map.get("nearest_buy_liq", 0)
    nearest_sell = liq_map.get("nearest_sell_liq", 0)

    near_buy  = (nearest_buy > 0 and
                 abs(price - nearest_buy) / price < tolerance_pct)
    near_sell = (nearest_sell > 0 and
                 abs(price - nearest_sell) / price < tolerance_pct)

    if near_buy:
        logger.debug(
            f"⚠️ Near buy-side liq ${nearest_buy:,.2f} — wait"
        )
    if near_sell:
        logger.debug(
            f"⚠️ Near sell-side liq ${nearest_sell:,.2f} — wait"
        )

    return {
        "near_buy_liq"  : near_buy,
        "near_sell_liq" : near_sell,
        "should_wait"   : near_buy or near_sell,
    }


def get_liquidity_targets(direction: str,
                           liq_map: dict) -> list:
    """
    Ambil target harga berdasarkan liquidity pools.
    Long  → target buy-side liquidity (swing highs)
    Short → target sell-side liquidity (swing lows)
    """
    if direction == "LONG":
        targets = liq_map.get("buy_side_liquidity", [])
    else:
        targets = liq_map.get("sell_side_liquidity", [])

    return [t["price"] for t in targets if t["price"] > 0]


# ── HELPERS ──────────────────────────────────────────────────

def _calc_swing_strength(prices: list, idx: int,
                          swing_type: str) -> int:
    """Hitung kekuatan swing 1-5."""
    if idx < 3 or idx >= len(prices) - 3:
        return 1
    center = prices[idx]
    nearby = [prices[idx-3], prices[idx-2], prices[idx-1],
              prices[idx+1], prices[idx+2], prices[idx+3]]

    if swing_type == "high":
        diffs = [(center - p) / center * 100 for p in nearby]
    else:
        diffs = [(p - center) / center * 100 for p in nearby]

    avg_diff = sum(diffs) / len(diffs)
    if avg_diff > 0.3:
        return 5
    elif avg_diff > 0.2:
        return 4
    elif avg_diff > 0.1:
        return 3
    elif avg_diff > 0.05:
        return 2
    return 1
