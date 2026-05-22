# ============================================================
# engine/failed_auction.py
# Dedicated Failed Auction Detector
# Konsep Andrea & Fabio: breakout yang gagal kembali ke range
# Setup dengan probabilitas tertinggi dalam sistem ini
# ============================================================

from loguru import logger
from config import OF_CONFIG
from data.binance_ws import get_recent_trades, get_buy_sell_volume


def detect_failed_auction(klines: list,
                           vp: dict,
                           lookback: int = 10) -> dict:
    """
    Deteksi failed auction dari klines + volume profile.

    FAILED AUCTION BEARISH (SHORT setup):
    1. Harga break ke atas VAH atau recent high
    2. Volume saat break RENDAH (tidak ada conviction)
       ATAU absorption terjadi di atas level
    3. Candle menutup KEMBALI di bawah level
    4. Candle berikutnya konfirmasi bearish

    FAILED AUCTION BULLISH (LONG setup):
    → Kebalikannya

    Andrea: "8 out of 10 times price reverts after failed auction"
    Fabio:  "The setup is trying to unfold — failed auction at VAH"
    """
    if len(klines) < lookback + 2 or not vp:
        return _empty_result()

    vah    = vp.get("vah", 0)
    val    = vp.get("val", 0)
    poc    = vp.get("poc", 0)
    recent = klines[-lookback:]

    # Recent swing high/low
    swing_high = max(k["high"] for k in recent[:-2])
    swing_low  = min(k["low"]  for k in recent[:-2])

    prev   = klines[-2]   # candle sebelum terakhir
    latest = klines[-1]   # candle terakhir

    # Average volume
    avg_vol = sum(k["volume"] for k in recent) / len(recent)

    # ── FAILED AUCTION BEARISH ───────────────────────────────
    bear_at_vah   = _check_bear_fa(prev, latest, vah, avg_vol,
                                    label="VAH")
    bear_at_swing = _check_bear_fa(prev, latest, swing_high, avg_vol,
                                    label="SwingHigh")

    # ── FAILED AUCTION BULLISH ───────────────────────────────
    bull_at_val   = _check_bull_fa(prev, latest, val, avg_vol,
                                    label="VAL")
    bull_at_swing = _check_bull_fa(prev, latest, swing_low, avg_vol,
                                    label="SwingLow")

    # Gabungkan hasil
    bear_fa = bear_at_vah or bear_at_swing
    bull_fa = bull_at_val or bull_at_swing

    # Tidak bisa keduanya sekaligus
    if bear_fa and bull_fa:
        # Pilih yang lebih kuat (lebih dekat ke level)
        bear_dist = abs(latest["close"] - vah) if bear_at_vah else abs(latest["close"] - swing_high)
        bull_dist = abs(latest["close"] - val) if bull_at_val else abs(latest["close"] - swing_low)
        if bear_dist < bull_dist:
            bull_fa = False
        else:
            bear_fa = False

    result = {
        "bear_fa"       : bear_fa,
        "bull_fa"       : bull_fa,
        "bear_at_vah"   : bear_at_vah,
        "bear_at_swing" : bear_at_swing,
        "bull_at_val"   : bull_at_val,
        "bull_at_swing" : bull_at_swing,
        "level_tested"  : vah if bear_at_vah else (
                          val if bull_at_val else (
                          swing_high if bear_at_swing else swing_low)),
        "strength"      : _calc_strength(klines, bear_fa, bull_fa,
                                         avg_vol),
    }

    if bear_fa:
        logger.info(
            f"❌ FAILED AUCTION BEARISH | "
            f"Level: {'VAH' if bear_at_vah else 'SwingHigh'} "
            f"@ {result['level_tested']:.2f} | "
            f"Strength: {result['strength']}/10"
        )
    if bull_fa:
        logger.info(
            f"✅ FAILED AUCTION BULLISH | "
            f"Level: {'VAL' if bull_at_val else 'SwingLow'} "
            f"@ {result['level_tested']:.2f} | "
            f"Strength: {result['strength']}/10"
        )

    return result


def detect_failed_auction_realtime(price: float,
                                    vp: dict,
                                    seconds: int = 120) -> dict:
    """
    Deteksi failed auction dari data real-time WebSocket.
    Lebih cepat dari versi klines — cocok untuk scalping.

    Logika: harga keluar dari Value Area tapi kemudian
    buy/sell volume tidak mendukung kelanjutan arah tersebut.
    """
    if not vp:
        return _empty_result()

    vah      = vp.get("vah", price)
    val      = vp.get("val", price)
    flow     = get_buy_sell_volume(seconds=seconds)

    # Harga di atas VAH tapi delta negatif (seller dominan) = failed auction
    bear_fa_rt = (
        price > vah and
        flow["delta"] < 0 and
        abs(flow["delta_pct"]) > 30
    )

    # Harga di bawah VAL tapi delta positif (buyer dominan) = failed auction
    bull_fa_rt = (
        price < val and
        flow["delta"] > 0 and
        flow["delta_pct"] > 30
    )

    return {
        "bear_fa"  : bear_fa_rt,
        "bull_fa"  : bull_fa_rt,
        "realtime" : True,
        "price"    : price,
        "vah"      : vah,
        "val"      : val,
        "delta_pct": flow["delta_pct"],
        "strength" : min(10, int(abs(flow["delta_pct"]) / 10)),
    }


# ── PRIVATE HELPERS ──────────────────────────────────────────

def _check_bear_fa(prev: dict, latest: dict,
                   level: float, avg_vol: float,
                   label: str) -> bool:
    """
    Cek apakah ada failed auction bearish di level tertentu.
    Syarat:
    1. Candle prev: high menembus level (break ke atas)
    2. Candle prev: volume rendah (tidak ada conviction)
       ATAU close-nya sudah kembali ke bawah level
    3. Candle latest: close di bawah level (konfirmasi)
    4. Candle latest: bearish (close < open)
    """
    # Tolerance 0.1% dari level
    tol = level * 0.001

    breakout   = prev["high"] > level + tol
    low_volume = prev["volume"] < avg_vol * 1.2
    rejection  = latest["close"] < level - tol
    bearish    = latest["close"] < latest["open"]

    return breakout and low_volume and rejection and bearish


def _check_bull_fa(prev: dict, latest: dict,
                   level: float, avg_vol: float,
                   label: str) -> bool:
    """
    Cek apakah ada failed auction bullish di level tertentu.
    """
    tol = level * 0.001

    breakdown  = prev["low"] < level - tol
    low_volume = prev["volume"] < avg_vol * 1.2
    recovery   = latest["close"] > level + tol
    bullish    = latest["close"] > latest["open"]

    return breakdown and low_volume and recovery and bullish


def _calc_strength(klines: list,
                   is_bear: bool,
                   is_bull: bool,
                   avg_vol: float) -> int:
    """Hitung kekuatan sinyal 1-10."""
    if not (is_bear or is_bull):
        return 0

    latest = klines[-1]
    score  = 5  # base score

    # Volume konfirmasi (volume candle konfirmasi lebih tinggi = lebih kuat)
    if latest["volume"] > avg_vol * 1.5:
        score += 2
    elif latest["volume"] > avg_vol:
        score += 1

    # Body candle konfirmasi besar = lebih kuat
    body  = abs(latest["close"] - latest["open"])
    rng   = latest["high"] - latest["low"]
    if rng > 0 and body / rng > 0.6:
        score += 2

    # Wick rejection
    if is_bear:
        wick = latest["high"] - max(latest["open"], latest["close"])
        if wick / rng > 0.3 if rng > 0 else False:
            score += 1
    elif is_bull:
        wick = min(latest["open"], latest["close"]) - latest["low"]
        if wick / rng > 0.3 if rng > 0 else False:
            score += 1

    return min(score, 10)


def _empty_result() -> dict:
    return {
        "bear_fa": False, "bull_fa": False,
        "bear_at_vah": False, "bear_at_swing": False,
        "bull_at_val": False, "bull_at_swing": False,
        "level_tested": 0, "strength": 0,
    }
