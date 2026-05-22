# ============================================================
# engine/daily_bias.py — LAYER 1: WORLD VIEW
# ERIDA Trading Bot
#
# Menentukan bias harian sebelum sesi dimulai.
# Update: tambah detect_session_divergence()
# untuk handle skenario Asia/London naik → NY turun
# ============================================================

import time
from datetime import datetime, timezone
from loguru import logger
from data.binance_rest import get_klines, get_funding_rate
from engine.volume_profile import build_volume_profile


# Cache bias harian
_daily_bias_cache = {
    "date"      : None,
    "bias"      : "NEUTRAL",
    "score"     : 0,
    "data"      : {},
    "updated_at": 0,
}


def get_daily_bias(force_refresh: bool = False) -> dict:
    """
    Hitung bias harian lengkap.
    Di-cache per jam — tidak hitung ulang setiap 30 detik.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if (not force_refresh and
            _daily_bias_cache["date"] == today and
            time.time() - _daily_bias_cache["updated_at"] < 3600):
        return _daily_bias_cache["data"]

    logger.info("📅 Calculating daily bias...")

    result = _calculate_daily_bias()

    _daily_bias_cache["date"]       = today
    _daily_bias_cache["bias"]       = result["bias"]
    _daily_bias_cache["score"]      = result["score"]
    _daily_bias_cache["data"]       = result
    _daily_bias_cache["updated_at"] = time.time()

    logger.info(
        f"📅 Daily Bias: {result['bias']} "
        f"(score: {result['score']}) | "
        f"HTF: {result['htf_trend']} | "
        f"Zone: {result['premium_discount']} | "
        f"Session div: {result.get('session_divergence', {}).get('type', 'NONE')}"
    )

    return result


def _calculate_daily_bias() -> dict:
    """Hitung bias harian dari semua sumber data."""
    score   = 0
    reasons = []

    # ── 1. HIGHER TIMEFRAME TREND ────────────────────────────
    try:
        klines_4h  = get_klines(interval="4h", limit=50)
        trend_4h   = _calc_ema_trend(klines_4h, period=20)

        klines_1h  = get_klines(interval="1h", limit=50)
        trend_1h   = _calc_ema_trend(klines_1h, period=20)

        klines_15m = get_klines(interval="15m", limit=50)
        trend_15m  = _calc_ema_trend(klines_15m, period=20)

        if trend_4h == "UP":
            score += 30
            reasons.append("4H UP (+30)")
        elif trend_4h == "DOWN":
            score -= 30
            reasons.append("4H DOWN (-30)")

        if trend_1h == "UP":
            score += 20
            reasons.append("1H UP (+20)")
        elif trend_1h == "DOWN":
            score -= 20
            reasons.append("1H DOWN (-20)")

        if trend_15m == "UP":
            score += 10
            reasons.append("15M UP (+10)")
        elif trend_15m == "DOWN":
            score -= 10
            reasons.append("15M DOWN (-10)")

        up_count   = [trend_4h, trend_1h, trend_15m].count("UP")
        down_count = [trend_4h, trend_1h, trend_15m].count("DOWN")

        if up_count >= 2:
            htf_trend = "UP"
        elif down_count >= 2:
            htf_trend = "DOWN"
        else:
            htf_trend = "FLAT"

    except Exception as e:
        logger.error(f"HTF trend error: {e}")
        trend_4h = trend_1h = trend_15m = "FLAT"
        htf_trend = "FLAT"

    # ── 2. SESSION DIVERGENCE (BARU) ─────────────────────────
    # Deteksi Asia+London pump/dump sebelum NY
    # Ini penting untuk handle skenario:
    # "Asia naik → London naik → NY turun"
    session_div = detect_session_divergence()

    if session_div.get("divergence"):
        adjustment = session_div.get("bias_adjustment", 0)
        score     += adjustment
        div_type   = session_div.get("type", "")

        if "BEARISH" in div_type:
            reasons.append(
                f"Session div BEARISH "
                f"({session_div.get('combined', 0):.1f}%) "
                f"({adjustment})"
            )
        elif "BULLISH" in div_type:
            reasons.append(
                f"Session div BULLISH "
                f"({session_div.get('combined', 0):.1f}%) "
                f"(+{adjustment})"
            )

        logger.info(
            f"📊 Session Divergence: {div_type} | "
            f"Asia: {session_div.get('asia_move', 0):.2f}% | "
            f"London: {session_div.get('london_move', 0):.2f}% | "
            f"Adj: {adjustment}"
        )

    # ── 3. DAILY VOLUME PROFILE ──────────────────────────────
    daily_high = daily_low = poc = vah = val = 0
    premium_discount = "FAIR"

    try:
        klines_1m_day = get_klines(interval="1m", limit=1440)

        if klines_1m_day:
            daily_high = max(k["high"] for k in klines_1m_day)
            daily_low  = min(k["low"]  for k in klines_1m_day)
            current    = klines_1m_day[-1]["close"]

            vp = build_volume_profile(klines_1m_day, bins=100)
            if vp:
                poc = vp["poc"]
                vah = vp["vah"]
                val = vp["val"]

            # Premium/Discount zone (ICT)
            daily_range   = daily_high - daily_low
            premium_line  = daily_low + (daily_range * 0.705)
            discount_line = daily_low + (daily_range * 0.295)

            if current > premium_line:
                premium_discount = "PREMIUM"
                score -= 15
                reasons.append("PREMIUM zone (-15)")
            elif current < discount_line:
                premium_discount = "DISCOUNT"
                score += 15
                reasons.append("DISCOUNT zone (+15)")
            else:
                premium_discount = "FAIR"

            if poc > 0:
                if current > poc * 1.002:
                    score += 5
                    reasons.append("Above POC (+5)")
                elif current < poc * 0.998:
                    score -= 5
                    reasons.append("Below POC (-5)")

    except Exception as e:
        logger.error(f"Daily VP error: {e}")

    # ── 4. PREVIOUS DAY HIGH/LOW ─────────────────────────────
    try:
        klines_1d = get_klines(interval="1d", limit=3)
        if len(klines_1d) >= 2:
            prev_day   = klines_1d[-2]
            prev_high  = prev_day["high"]
            prev_low   = prev_day["low"]
            prev_close = prev_day["close"]
            current    = klines_1d[-1]["close"]

            if current > prev_high:
                score += 15
                reasons.append("Above prev high (+15)")
            elif current > prev_close:
                score += 8
                reasons.append("Above prev close (+8)")
            elif current < prev_low:
                score -= 15
                reasons.append("Below prev low (-15)")
            elif current < prev_close:
                score -= 8
                reasons.append("Below prev close (-8)")

    except Exception as e:
        logger.error(f"Previous day error: {e}")

    # ── 5. VWAP ──────────────────────────────────────────────
    vwap = 0.0
    try:
        klines_1m = get_klines(interval="1m", limit=480)
        if klines_1m:
            vwap    = _calculate_vwap(klines_1m)
            current = klines_1m[-1]["close"]

            if current > vwap * 1.001:
                score += 10
                reasons.append("Above VWAP (+10)")
            elif current < vwap * 0.999:
                score -= 10
                reasons.append("Below VWAP (-10)")

    except Exception as e:
        logger.error(f"VWAP error: {e}")

    # ── 6. FUNDING RATE ──────────────────────────────────────
    try:
        funding = get_funding_rate()
        if funding < -0.001:
            score += 10
            reasons.append("Funding very neg (+10)")
        elif funding < -0.0003:
            score += 5
            reasons.append("Funding neg (+5)")
        elif funding > 0.001:
            score -= 10
            reasons.append("Funding very pos (-10)")
        elif funding > 0.0003:
            score -= 5
            reasons.append("Funding pos (-5)")
    except Exception as e:
        logger.error(f"Funding error: {e}")

    # ── FINAL BIAS ───────────────────────────────────────────
    score = max(-100, min(100, score))

    if score >= 40:
        bias       = "BULL"
        only_long  = True
        only_short = False
        skip_today = False
    elif score <= -40:
        bias       = "BEAR"
        only_long  = False
        only_short = True
        skip_today = False
    elif 20 <= score < 40:
        bias       = "WEAK_BULL"
        only_long  = False
        only_short = False
        skip_today = False
    elif -40 < score <= -20:
        bias       = "WEAK_BEAR"
        only_long  = False
        only_short = False
        skip_today = False
    else:
        bias       = "NEUTRAL"
        only_long  = False
        only_short = False
        skip_today = True

    return {
        "bias"               : bias,
        "score"              : score,
        "only_long"          : only_long,
        "only_short"         : only_short,
        "skip_today"         : skip_today,
        "htf_trend"          : htf_trend,
        "trend_4h"           : trend_4h,
        "trend_1h"           : trend_1h,
        "trend_15m"          : trend_15m,
        "premium_discount"   : premium_discount,
        "session_divergence" : session_div,
        "reason"             : " | ".join(reasons[:5]),
        "levels"             : {
            "daily_high" : daily_high,
            "daily_low"  : daily_low,
            "poc"        : poc,
            "vah"        : vah,
            "val"        : val,
            "vwap"       : vwap,
        },
    }


def detect_session_divergence() -> dict:
    """
    Deteksi divergence antara session Asia/London
    dengan potensi arah NY.

    Jika Asia/London naik terlalu cepat:
    → Kemungkinan NY akan distribusi (turun)
    → Bias tidak bisa hanya dari HTF trend

    Jika Asia/London turun terlalu cepat:
    → Kemungkinan NY akan akumulasi (naik)

    Konsep ICT: "London creates the liquidity
                  that New York hunts"
    """
    try:
        klines_1h = get_klines(interval="1h", limit=24)
        if not klines_1h:
            return {"divergence": False}

        now  = datetime.now(timezone.utc)
        hour = now.hour

        # Hitung range Asia session (00-07 UTC)
        asia_candles = [k for k in klines_1h
                        if _is_in_session(k, 0, 7)]

        # Hitung range London session (07-13 UTC)
        london_candles = [k for k in klines_1h
                          if _is_in_session(k, 7, 13)]

        if not asia_candles or not london_candles:
            return {"divergence": False}

        asia_open  = asia_candles[0]["open"]
        asia_close = asia_candles[-1]["close"]
        asia_move  = (asia_close - asia_open) / asia_open * 100

        london_open  = london_candles[0]["open"]
        london_close = london_candles[-1]["close"]
        london_move  = (london_close - london_open) / london_open * 100

        combined_move = asia_move + london_move

        # Asia+London naik >1.5% = kemungkinan NY reversal turun
        if combined_move > 1.5:
            return {
                "divergence"     : True,
                "type"           : "BEARISH_NY_POTENTIAL",
                "asia_move"      : round(asia_move, 2),
                "london_move"    : round(london_move, 2),
                "combined"       : round(combined_move, 2),
                "note"           : "Asia+London pump — NY dump potential",
                "bias_adjustment": -30,
            }

        # Asia+London turun >1.5% = kemungkinan NY reversal naik
        elif combined_move < -1.5:
            return {
                "divergence"     : True,
                "type"           : "BULLISH_NY_POTENTIAL",
                "asia_move"      : round(asia_move, 2),
                "london_move"    : round(london_move, 2),
                "combined"       : round(combined_move, 2),
                "note"           : "Asia+London dump — NY pump potential",
                "bias_adjustment": +30,
            }

        return {
            "divergence"  : False,
            "asia_move"   : round(asia_move, 2),
            "london_move" : round(london_move, 2),
            "combined"    : round(combined_move, 2),
        }

    except Exception as e:
        logger.error(f"Session divergence error: {e}")
        return {"divergence": False}


# ── TECHNICAL HELPERS ────────────────────────────────────────

def _is_in_session(kline: dict,
                    start_hour: int,
                    end_hour: int) -> bool:
    """
    Cek apakah kline masuk dalam rentang jam UTC.
    Dipakai oleh detect_session_divergence().
    """
    try:
        ts   = kline.get("open_time", 0)
        if isinstance(ts, str):
            from datetime import datetime
            dt   = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            hour = dt.hour
        else:
            # timestamp dalam milliseconds
            hour = (int(ts) // 3_600_000) % 24
        return start_hour <= hour < end_hour
    except Exception:
        return False


def _calc_ema_trend(klines: list, period: int = 20) -> str:
    """Deteksi trend dari EMA."""
    if len(klines) < period:
        return "FLAT"
    closes = [k["close"] for k in klines]
    k_val  = 2 / (period + 1)
    ema    = closes[0]
    for c in closes[1:]:
        ema = c * k_val + ema * (1 - k_val)
    latest = closes[-1]
    if latest > ema * 1.005:
        return "UP"
    elif latest < ema * 0.995:
        return "DOWN"
    return "FLAT"


def _calculate_vwap(klines: list) -> float:
    """Hitung VWAP dari klines."""
    total_pv = 0.0
    total_v  = 0.0
    for k in klines:
        typical  = (k["high"] + k["low"] + k["close"]) / 3
        total_pv += typical * k["volume"]
        total_v  += k["volume"]
    return total_pv / total_v if total_v > 0 else 0.0


def get_key_levels(daily_bias: dict) -> dict:
    """Ambil level-level kunci untuk hari ini."""
    lvls = daily_bias.get("levels", {})
    return {
        "daily_high" : lvls.get("daily_high", 0),
        "daily_low"  : lvls.get("daily_low", 0),
        "poc"        : lvls.get("poc", 0),
        "vah"        : lvls.get("vah", 0),
        "val"        : lvls.get("val", 0),
        "vwap"       : lvls.get("vwap", 0),
    }
