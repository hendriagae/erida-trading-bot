# ============================================================
# engine/session_structure.py — LAYER 2 (bagian)
# ERIDA Trading Bot
#
# Analisa struktur sesi trading:
# → Opening Range Breakout (Fabio's favorite setup)
# → Session direction dari first breakout
# → Kill zones (ICT) — waktu probabilitas tertinggi
# → VWAP sebagai dynamic support/resistance
#
# Fabio: "80% of sessions that break the opening range
#         continue in that direction"
# ============================================================

from datetime import datetime, timezone
from loguru import logger
from data.binance_rest import get_klines


# Cache session data
_session_cache = {
    "date"           : None,
    "opening_range"  : {},
    "session_dir"    : None,
    "updated_at"     : 0,
}


def get_session_structure() -> dict:
    """
    Analisa struktur sesi hari ini.
    Opening range = high/low dari 30 menit pertama NY session.
    """
    now  = datetime.now(timezone.utc)
    date = now.strftime("%Y-%m-%d")

    # Return cache jika masih valid (update setiap 15 menit)
    import time
    if (_session_cache["date"] == date and
            time.time() - _session_cache["updated_at"] < 900):
        return _session_cache

    result = _calculate_session()
    _session_cache.update(result)
    _session_cache["date"]       = date
    _session_cache["updated_at"] = time.time()

    return _session_cache


def _calculate_session() -> dict:
    """Hitung semua data sesi."""
    now  = datetime.now(timezone.utc)
    hour = now.hour

    # ── OPENING RANGE ────────────────────────────────────────
    # NY session open = 13:30 UTC
    # Opening range = 13:30-14:00 UTC (30 menit)
    opening_range = {}
    session_dir   = "UNDEFINED"

    try:
        # Ambil klines 5 menit dari awal sesi hari ini
        klines_5m = get_klines(interval="5m", limit=200)

        if klines_5m:
            current_price = klines_5m[-1]["close"]

            # Filter klines yang ada di opening range window
            # (13:30-14:00 UTC = 6 candle 5 menit)
            # Kita gunakan pendekatan: ambil high/low 6 candle
            # setelah NY open (sekitar 2-3 jam yang lalu)
            or_klines = klines_5m[-150:-144]  # sekitar 12.5 jam lalu

            if len(or_klines) >= 4:
                or_high = max(k["high"] for k in or_klines)
                or_low  = min(k["low"]  for k in or_klines)
                or_mid  = (or_high + or_low) / 2

                opening_range = {
                    "high" : or_high,
                    "low"  : or_low,
                    "mid"  : or_mid,
                    "range": or_high - or_low,
                }

                # Tentukan session direction dari breakout
                if current_price > or_high * 1.001:
                    session_dir = "BULLISH"
                elif current_price < or_low * 0.999:
                    session_dir = "BEARISH"
                else:
                    session_dir = "INSIDE_RANGE"

    except Exception as e:
        logger.error(f"Opening range error: {e}")

    # ── VWAP ─────────────────────────────────────────────────
    vwap = _calculate_session_vwap()

    # ── KILL ZONES ───────────────────────────────────────────
    kz = _get_kill_zone_status()

    # ── SESSION QUALITY ──────────────────────────────────────
    # Seberapa bagus kondisi trading saat ini
    quality = _assess_session_quality(kz, session_dir, hour)

    result = {
        "session_dir"    : session_dir,
        "opening_range"  : opening_range,
        "vwap"           : vwap,
        "kill_zone"      : kz,
        "session_quality": quality,
        "current_hour"   : hour,
        "is_tradeable"   : quality["score"] >= 50,
    }

    logger.info(
        f"📈 Session | Dir: {session_dir} | "
        f"KZ: {kz['name']} | "
        f"VWAP: ${vwap:,.2f} | "
        f"Quality: {quality['score']}/100"
    )

    return result


def _get_kill_zone_status() -> dict:
    """
    Identifikasi kill zone saat ini.
    Kill zone = waktu probabilitas trade terbaik (ICT).
    """
    now          = datetime.now(timezone.utc)
    hour         = now.hour
    minute       = now.minute
    time_decimal = hour + minute / 60

    # Asia Kill Zone: 00:00-02:00 UTC
    if 0 <= time_decimal < 2:
        return {
            "active" : True,
            "name"   : "Asia KZ",
            "quality": 50,
            "note"   : "Low volume, scalp only",
        }

    # London Kill Zone: 07:00-09:30 UTC
    if 7.0 <= time_decimal < 9.5:
        return {
            "active" : True,
            "name"   : "London KZ",
            "quality": 80,
            "note"   : "High probability reversals",
        }

    # NY Kill Zone: 12:00-14:00 UTC (NY open + 30min)
    if 12.0 <= time_decimal < 14.0:
        return {
            "active" : True,
            "name"   : "NY Open KZ",
            "quality": 95,
            "note"   : "Highest probability — best setups",
        }

    # NY Lunch: 16:00-17:00 UTC — hindari
    if 16.0 <= time_decimal < 17.0:
        return {
            "active" : False,
            "name"   : "NY Lunch",
            "quality": 20,
            "note"   : "Low volume, avoid trading",
        }

    # NY Power Hour: 19:00-20:00 UTC
    if 19.0 <= time_decimal < 20.0:
        return {
            "active" : True,
            "name"   : "NY Power Hour",
            "quality": 75,
            "note"   : "Strong directional moves",
        }

    # Outside kill zone
    return {
        "active" : False,
        "name"   : "Off-hours",
        "quality": 30,
        "note"   : "Avoid — low probability",
    }


def _calculate_session_vwap() -> float:
    """Hitung VWAP dari awal sesi hari ini."""
    try:
        # Ambil klines 1 menit dari 8 jam terakhir
        klines = get_klines(interval="1m", limit=480)
        if not klines:
            return 0.0
        total_pv = sum(
            ((k["high"] + k["low"] + k["close"]) / 3) * k["volume"]
            for k in klines
        )
        total_v  = sum(k["volume"] for k in klines)
        return total_pv / total_v if total_v > 0 else 0.0
    except Exception as e:
        logger.error(f"VWAP error: {e}")
        return 0.0


def _assess_session_quality(kz: dict,
                              session_dir: str,
                              hour: int) -> dict:
    """
    Nilai kualitas sesi untuk trading.
    Score 0-100:
    → 80+: Kondisi ideal, trade penuh
    → 60-79: Kondisi baik, trade normal
    → 40-59: Kondisi cukup, trade kecil
    → <40: Kondisi buruk, skip
    """
    score   = kz["quality"]
    reasons = [kz["name"]]

    # Hari buruk (Fabio: Monday & Friday)
    from datetime import datetime, timezone
    weekday = datetime.now(timezone.utc).weekday()
    if weekday == 0:  # Monday
        score  -= 20
        reasons.append("Monday (-20)")
    elif weekday == 4:  # Friday
        score  -= 15
        reasons.append("Friday (-15)")
    elif weekday in [1, 2, 3]:  # Tue-Thu best
        score  += 10
        reasons.append("Good day (+10)")

    # Session direction bonus
    if session_dir in ["BULLISH", "BEARISH"]:
        score  += 15
        reasons.append(f"Clear direction (+15)")
    elif session_dir == "INSIDE_RANGE":
        score  -= 10
        reasons.append("Inside OR (-10)")

    score = max(0, min(100, score))

    return {
        "score"  : score,
        "reasons": reasons,
        "grade"  : ("EXCELLENT" if score >= 80 else
                    "GOOD"      if score >= 60 else
                    "FAIR"      if score >= 40 else
                    "POOR"),
    }


def is_breakout_from_or(price: float,
                         session: dict) -> dict:
    """
    Cek apakah harga baru breakout dari opening range.
    Fabio's ORB setup — probabilitas 80%.
    """
    or_data = session.get("opening_range", {})
    if not or_data:
        return {"breakout": False, "direction": None}

    or_high = or_data.get("high", 0)
    or_low  = or_data.get("low", 0)
    tol     = (or_high - or_low) * 0.1  # 10% tolerance

    if price > or_high + tol:
        return {
            "breakout"  : True,
            "direction" : "BULLISH",
            "level"     : or_high,
            "note"      : "ORB Long — 80% continuation",
        }
    elif price < or_low - tol:
        return {
            "breakout"  : True,
            "direction" : "BEARISH",
            "level"     : or_low,
            "note"      : "ORB Short — 80% continuation",
        }

    return {"breakout": False, "direction": None}
