# ============================================================
# engine/vwap_calculator.py
# ERIDA Trading Bot
#
# VWAP = Volume Weighted Average Price
# Dipakai oleh hampir semua institutional trader
# sebagai referensi "fair value" intraday.
#
# Konsep:
# → Price di atas VWAP = bullish bias
# → Price di bawah VWAP = bearish bias
# → First touch VWAP setelah trend = entry terbaik
# → VWAP rejection = high probability reversal
#
# Tambahan: Anchored VWAP dari swing points penting
# ============================================================

from loguru import logger
from data.binance_rest import get_klines


def calculate_vwap(klines: list) -> float:
    """Hitung VWAP standard dari klines."""
    if not klines:
        return 0.0
    total_pv = 0.0
    total_v  = 0.0
    for k in klines:
        typical  = (k["high"] + k["low"] + k["close"]) / 3
        total_pv += typical * k["volume"]
        total_v  += k["volume"]
    return total_pv / total_v if total_v > 0 else 0.0


def calculate_anchored_vwap(klines: list,
                             anchor_idx: int) -> float:
    """
    Anchored VWAP dari titik tertentu.
    Anchor = swing high/low atau event penting.
    Lebih akurat dari VWAP biasa untuk
    identifikasi entry di retracement.
    """
    if anchor_idx >= len(klines):
        return calculate_vwap(klines)
    anchored = klines[anchor_idx:]
    return calculate_vwap(anchored)


def get_vwap_levels(klines: list) -> dict:
    """
    Hitung VWAP + Standard Deviation bands.
    Mirip Bollinger Bands tapi berbasis volume.

    VWAP + 1 SD = first resistance
    VWAP + 2 SD = premium zone (overbought)
    VWAP - 1 SD = first support
    VWAP - 2 SD = discount zone (oversold)
    """
    if not klines:
        return {}

    vwap     = calculate_vwap(klines)
    if vwap == 0:
        return {}

    # Hitung variance berbasis volume
    total_v  = sum(k["volume"] for k in klines)
    if total_v == 0:
        return {}

    variance = sum(
        k["volume"] * ((k["high"] + k["low"] + k["close"]) / 3
                       - vwap) ** 2
        for k in klines
    ) / total_v

    import math
    std_dev = math.sqrt(variance)

    levels = {
        "vwap"       : round(vwap, 2),
        "upper_1sd"  : round(vwap + std_dev, 2),
        "upper_2sd"  : round(vwap + 2 * std_dev, 2),
        "lower_1sd"  : round(vwap - std_dev, 2),
        "lower_2sd"  : round(vwap - 2 * std_dev, 2),
        "std_dev"    : round(std_dev, 2),
    }

    logger.debug(
        f"VWAP: ${vwap:,.2f} | "
        f"+1SD: ${levels['upper_1sd']:,.2f} | "
        f"-1SD: ${levels['lower_1sd']:,.2f}"
    )

    return levels


def analyze_price_vs_vwap(price: float,
                           vwap_levels: dict) -> dict:
    """
    Analisa posisi harga terhadap VWAP.
    Return signal bias dan kualitas entry.
    """
    if not vwap_levels:
        return {"bias": "NEUTRAL", "quality": 0}

    vwap     = vwap_levels.get("vwap", 0)
    upper_1  = vwap_levels.get("upper_1sd", 0)
    upper_2  = vwap_levels.get("upper_2sd", 0)
    lower_1  = vwap_levels.get("lower_1sd", 0)
    lower_2  = vwap_levels.get("lower_2sd", 0)

    if price > upper_2:
        return {
            "bias"    : "STRONG_BULL",
            "quality" : 30,  # terlalu mahal untuk long
            "zone"    : "EXTREME_PREMIUM",
            "note"    : "Price too extended, wait for pullback",
        }
    elif price > upper_1:
        return {
            "bias"    : "BULL",
            "quality" : 60,
            "zone"    : "PREMIUM",
            "note"    : "Bullish but stretched, short preferred",
        }
    elif price > vwap:
        return {
            "bias"    : "BULL",
            "quality" : 80,
            "zone"    : "ABOVE_VWAP",
            "note"    : "Bullish bias, look for long on dip to VWAP",
        }
    elif price > lower_1:
        return {
            "bias"    : "BEAR",
            "quality" : 80,
            "zone"    : "BELOW_VWAP",
            "note"    : "Bearish bias, look for short on rally to VWAP",
        }
    elif price > lower_2:
        return {
            "bias"    : "BEAR",
            "quality" : 60,
            "zone"    : "DISCOUNT",
            "note"    : "Bearish but stretched, long preferred",
        }
    else:
        return {
            "bias"    : "STRONG_BEAR",
            "quality" : 30,
            "zone"    : "EXTREME_DISCOUNT",
            "note"    : "Price too extended down, wait for bounce",
        }


def detect_vwap_rejection(klines: list,
                           vwap: float,
                           tolerance_pct: float = 0.001) -> dict:
    """
    Deteksi rejection di VWAP.
    Ini adalah salah satu setup dengan probabilitas tertinggi:

    → Price turun ke VWAP
    → Candle menunjukkan rejection (wick panjang, body kecil)
    → Close kembali di atas VWAP
    → Entry LONG dengan SL di bawah VWAP

    Sebaliknya untuk SHORT.
    """
    if not klines or vwap == 0:
        return {"rejected": False, "direction": None}

    latest  = klines[-1]
    prev    = klines[-2] if len(klines) >= 2 else latest
    tol     = vwap * tolerance_pct

    # Hitung body dan wick
    body    = abs(latest["close"] - latest["open"])
    total   = latest["high"] - latest["low"]
    if total == 0:
        return {"rejected": False, "direction": None}

    body_ratio = body / total
    lower_wick = min(latest["open"], latest["close"]) - latest["low"]
    upper_wick = latest["high"] - max(latest["open"], latest["close"])

    # Bullish rejection: menyentuh VWAP dari atas, bounce naik
    bull_reject = (
        latest["low"] <= vwap + tol and    # menyentuh VWAP
        latest["close"] > vwap and         # close di atas VWAP
        lower_wick > body * 1.5 and        # wick bawah panjang
        body_ratio < 0.4                   # body kecil
    )

    # Bearish rejection: menyentuh VWAP dari bawah, bounce turun
    bear_reject = (
        latest["high"] >= vwap - tol and   # menyentuh VWAP
        latest["close"] < vwap and         # close di bawah VWAP
        upper_wick > body * 1.5 and        # wick atas panjang
        body_ratio < 0.4
    )

    if bull_reject:
        logger.info(
            f"🎯 VWAP Bullish Rejection @ ${vwap:,.2f} | "
            f"High prob LONG setup"
        )
        return {
            "rejected"  : True,
            "direction" : "BULLISH",
            "level"     : vwap,
            "quality"   : 85,
        }

    if bear_reject:
        logger.info(
            f"🎯 VWAP Bearish Rejection @ ${vwap:,.2f} | "
            f"High prob SHORT setup"
        )
        return {
            "rejected"  : True,
            "direction" : "BEARISH",
            "level"     : vwap,
            "quality"   : 85,
        }

    return {"rejected": False, "direction": None, "quality": 0}


def get_vwap_summary(price: float = None) -> dict:
    """Helper: ambil semua VWAP data sekaligus."""
    try:
        klines_1m = get_klines(interval="1m", limit=480)
        if not klines_1m:
            return {}

        if price is None:
            price = klines_1m[-1]["close"]

        vwap_levels = get_vwap_levels(klines_1m)
        vwap        = vwap_levels.get("vwap", 0)
        analysis    = analyze_price_vs_vwap(price, vwap_levels)
        rejection   = detect_vwap_rejection(klines_1m, vwap)

        return {
            "vwap"      : vwap,
            "levels"    : vwap_levels,
            "analysis"  : analysis,
            "rejection" : rejection,
            "price"     : price,
        }
    except Exception as e:
        logger.error(f"VWAP summary error: {e}")
        return {}
