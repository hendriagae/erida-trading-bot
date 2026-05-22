# ============================================================
# engine/regime_detector.py
# Deteksi market regime untuk memilih model yang tepat
# Andrea: "sistem harus adaptif terhadap market regime"
# Fabio: "trending day = Model A, consolidation = Model B"
# ============================================================

import math
from loguru import logger
from config import REGIME_CONFIG
from data.binance_rest import get_funding_rate, get_open_interest
from data.binance_ws import get_buy_sell_volume


def detect_regime(klines: list,
                  funding_rate: float = 0.0,
                  oi_change_pct: float = 0.0) -> dict:
    """
    Deteksi regime pasar dari multiple indicators.

    Regimes:
    - trending_strong  : ADX tinggi, volume konsisten searah
    - trending_normal  : trending tapi lemah
    - ranging          : harga bolak-balik dalam range
    - high_volatility  : spike volatilitas tiba-tiba
    - crisis           : extreme volatility (skip trading)

    Fabio: "70% of the time market is stationary (ranging).
            I want to make money ALSO when stationary."
    """
    if len(klines) < 20:
        return _default_regime()

    closes  = [k["close"] for k in klines]
    highs   = [k["high"]  for k in klines]
    lows    = [k["low"]   for k in klines]
    volumes = [k["volume"] for k in klines]

    # ── INDICATORS ───────────────────────────────────────────
    adx         = _calculate_adx(highs, lows, closes, period=14)
    atr         = _calculate_atr(highs, lows, closes, period=14)
    atr_pct     = atr / closes[-1] * 100
    vol_ratio   = volumes[-1] / (sum(volumes[-20:]) / 20)
    price_range = (max(closes[-20:]) - min(closes[-20:])) / closes[-1] * 100

    # ── REGIME CLASSIFICATION ────────────────────────────────

    # Crisis: ATR sangat tinggi atau volume spike ekstrem
    if atr_pct > 1.5 or vol_ratio > 4.0:
        regime = "crisis"
        confidence = 90

    # High volatility: ATR tinggi
    elif atr_pct > 0.8 or vol_ratio > 2.5:
        regime = "high_volatility"
        confidence = 80

    # Trending strong: ADX tinggi + volume konsisten
    elif adx > 30 and vol_ratio > 1.2:
        regime = "trending_strong"
        confidence = 85

    # Trending normal: ADX moderat
    elif adx > REGIME_CONFIG["adx_trending"]:
        regime = "trending_normal"
        confidence = 70

    # Ranging: ADX rendah, range sempit
    else:
        regime = "ranging"
        confidence = 75

    # ── AUTOCORRELATION (Andrea's concept) ───────────────────
    # Jika kemarin trending, hari ini kemungkinan trending juga
    autocorr = _calculate_autocorrelation(closes, lag=1)

    # ── FUNDING RATE MODIFIER ────────────────────────────────
    # Funding rate ekstrem bisa jadi catalyst untuk volatility
    if abs(funding_rate) > 0.002:
        if regime == "ranging":
            regime = "high_volatility"
            confidence = 65

    # ── OI CHANGE MODIFIER ───────────────────────────────────
    # OI naik signifikan + trending = memperkuat trend
    if oi_change_pct > 5 and "trending" in regime:
        confidence = min(confidence + 10, 95)

    result = {
        "regime"        : regime,
        "confidence"    : confidence,
        "adx"           : round(adx, 2),
        "atr_pct"       : round(atr_pct, 3),
        "vol_ratio"     : round(vol_ratio, 2),
        "price_range"   : round(price_range, 2),
        "autocorr"      : round(autocorr, 3),
        "size_mult"     : REGIME_CONFIG.get(regime, 0.5),
        "is_trending"   : "trending" in regime,
        "is_ranging"    : regime == "ranging",
        "is_dangerous"  : regime in ["high_volatility", "crisis"],
        "model_hint"    : "model_a" if "trending" in regime else "model_b",
    }

    logger.debug(
        f"Regime: {regime} ({confidence}%) | "
        f"ADX: {adx:.1f} | ATR%: {atr_pct:.3f} | "
        f"VolRatio: {vol_ratio:.1f}"
    )

    return result


def detect_regime_change(current: str,
                          previous: str) -> bool:
    """
    Deteksi apakah regime berubah.
    Fabio: "when regime changes, switch your approach immediately"
    """
    if current != previous:
        logger.info(f"🔄 REGIME CHANGE: {previous} → {current}")
        return True
    return False


def get_autocorrelation_bias(klines: list,
                              lookback: int = 5) -> str:
    """
    Orderflow autocorrelation dari Andrea.
    "If today has certain orderflow, tomorrow likely the same."
    """
    if len(klines) < lookback * 2:
        return "NEUTRAL"

    # Hitung arah N hari terakhir
    directions = []
    for i in range(lookback):
        k = klines[-(i+1)]
        directions.append("UP" if k["close"] > k["open"] else "DOWN")

    up_count   = directions.count("UP")
    down_count = directions.count("DOWN")

    if up_count >= lookback * 0.7:
        return "BULLISH_AUTOCORR"
    elif down_count >= lookback * 0.7:
        return "BEARISH_AUTOCORR"
    return "NEUTRAL"


# ── TECHNICAL CALCULATIONS ───────────────────────────────────

def _calculate_adx(highs: list, lows: list,
                   closes: list, period: int = 14) -> float:
    """Simplified ADX calculation."""
    if len(closes) < period + 1:
        return 20.0

    trs, pdms, ndms = [], [], []
    for i in range(1, len(closes)):
        h, l, pc = highs[i], lows[i], closes[i-1]
        tr  = max(h - l, abs(h - pc), abs(l - pc))
        pdm = max(h - highs[i-1], 0) if (h - highs[i-1]) > (lows[i-1] - l) else 0
        ndm = max(lows[i-1] - l, 0) if (lows[i-1] - l) > (h - highs[i-1]) else 0
        trs.append(tr)
        pdms.append(pdm)
        ndms.append(ndm)

    def smooth(data, p):
        s = sum(data[:p])
        result = [s]
        for v in data[p:]:
            s = s - s/p + v
            result.append(s)
        return result

    atr  = smooth(trs[-period*2:],  period)
    pdi  = smooth(pdms[-period*2:], period)
    ndi  = smooth(ndms[-period*2:], period)

    if not atr:
        return 20.0

    dx_list = []
    for i in range(len(atr)):
        pdi_val = (pdi[i] / atr[i] * 100) if atr[i] > 0 else 0
        ndi_val = (ndi[i] / atr[i] * 100) if atr[i] > 0 else 0
        s = pdi_val + ndi_val
        dx = (abs(pdi_val - ndi_val) / s * 100) if s > 0 else 0
        dx_list.append(dx)

    return sum(dx_list[-period:]) / period if dx_list else 20.0


def _calculate_atr(highs: list, lows: list,
                   closes: list, period: int = 14) -> float:
    """ATR calculation."""
    if len(closes) < period + 1:
        return closes[-1] * 0.005 if closes else 100.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i-1]),
                 abs(lows[i] - closes[i-1]))
        trs.append(tr)
    return sum(trs[-period:]) / period


def _calculate_autocorrelation(data: list, lag: int = 1) -> float:
    """Hitung autocorrelation sederhana."""
    if len(data) < lag + 2:
        return 0.0
    n   = len(data)
    mean = sum(data) / n
    c0  = sum((x - mean)**2 for x in data) / n
    if c0 == 0:
        return 0.0
    cl  = sum((data[i] - mean) * (data[i - lag] - mean)
              for i in range(lag, n)) / n
    return cl / c0


def _default_regime() -> dict:
    return {
        "regime"     : "ranging",
        "confidence" : 50,
        "adx"        : 20.0,
        "atr_pct"    : 0.3,
        "vol_ratio"  : 1.0,
        "size_mult"  : 0.5,
        "is_trending": False,
        "is_ranging" : True,
        "is_dangerous": False,
        "model_hint" : "model_b",
    }
