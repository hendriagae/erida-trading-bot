# ============================================================
# engine/volume_profile.py
# ERIDA Trading Bot
# Update: Session-based VP (bukan rolling 200 candle)
#
# VP dibangun dari awal sesi hari ini (UTC 00:00)
# sehingga VAH/VAL stabil sepanjang hari.
#
# Tambahan: Previous Day VP untuk referensi level
# ============================================================

import numpy as np
from datetime import datetime, timezone
from loguru import logger
from config import VP_CONFIG
from data.binance_rest import get_klines


# Cache VP session agar tidak rebuild setiap 30 detik
_session_vp_cache = {
    "date"       : None,
    "vp"         : {},
    "prev_vp"    : {},
    "updated_at" : 0,
}


def get_session_vp(force_refresh: bool = False) -> dict:
    """
    Ambil Volume Profile dari awal sesi hari ini.
    Di-cache dan hanya di-rebuild setiap 15 menit
    atau saat hari berganti.

    Ini jauh lebih stabil dari rolling 200 candle!
    VAH/VAL tidak bergerak setiap 30 detik.
    """
    import time
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Return cache jika masih valid (15 menit)
    if (not force_refresh and
            _session_vp_cache["date"] == today and
            time.time() - _session_vp_cache["updated_at"] < 900):
        return _session_vp_cache["vp"]

    try:
        # Ambil klines 1M dari 24 jam terakhir
        # (lebih dari cukup untuk satu sesi)
        klines_today = get_klines(interval="1m", limit=999)

        if not klines_today:
            return {}

        # Build session VP
        vp = build_volume_profile(klines_today, bins=100)

        # Build previous day VP untuk referensi
        klines_prev = get_klines(interval="1m", limit=999)
        prev_klines  = klines_prev[:1440] if len(klines_prev) >= 1440 else []
        prev_vp      = build_volume_profile(prev_klines, bins=100) if prev_klines else {}

        _session_vp_cache["date"]       = today
        _session_vp_cache["vp"]         = vp
        _session_vp_cache["prev_vp"]    = prev_vp
        _session_vp_cache["updated_at"] = time.time()

        if vp:
            logger.debug(
                f"Session VP built | "
                f"POC: {vp['poc']:.2f} | "
                f"VAH: {vp['vah']:.2f} | "
                f"VAL: {vp['val']:.2f}"
            )

        return vp

    except Exception as e:
        logger.error(f"Session VP error: {e}")
        return {}


def get_previous_day_vp() -> dict:
    """Ambil VP hari kemarin sebagai referensi level."""
    return _session_vp_cache.get("prev_vp", {})


def build_volume_profile(klines: list,
                          bins: int = 50) -> dict:
    """
    Build Volume Profile dari klines.
    Distribusi volume per price level.
    """
    if not klines:
        return {}

    all_highs = [k["high"] for k in klines]
    all_lows  = [k["low"]  for k in klines]
    price_max = max(all_highs)
    price_min = min(all_lows)
    tick_size = (price_max - price_min) / bins

    if tick_size == 0:
        return {}

    price_levels = np.linspace(price_min, price_max, bins + 1)
    volume_dist  = np.zeros(bins)

    for k in klines:
        lo, hi, vol = k["low"], k["high"], k["volume"]
        for i in range(bins):
            level_lo = price_levels[i]
            level_hi = price_levels[i + 1]
            overlap  = min(hi, level_hi) - max(lo, level_lo)
            if overlap > 0:
                candle_range = hi - lo if (hi - lo) > 0 else tick_size
                volume_dist[i] += vol * (overlap / candle_range)

    # Point of Control
    poc_idx   = int(np.argmax(volume_dist))
    poc_price = (price_levels[poc_idx] + price_levels[poc_idx + 1]) / 2

    # Value Area (70% volume)
    total_vol    = volume_dist.sum()
    target_vol   = total_vol * VP_CONFIG["value_area_pct"]
    included_vol = volume_dist[poc_idx]
    lo_idx       = poc_idx
    hi_idx       = poc_idx

    while included_vol < target_vol:
        can_up   = hi_idx < bins - 1
        can_down = lo_idx > 0
        if not can_up and not can_down:
            break
        if can_up and can_down:
            if volume_dist[hi_idx + 1] >= volume_dist[lo_idx - 1]:
                hi_idx       += 1
                included_vol += volume_dist[hi_idx]
            else:
                lo_idx       -= 1
                included_vol += volume_dist[lo_idx]
        elif can_up:
            hi_idx       += 1
            included_vol += volume_dist[hi_idx]
        else:
            lo_idx       -= 1
            included_vol += volume_dist[lo_idx]

    vah = price_levels[hi_idx + 1]
    val = price_levels[lo_idx]

    # LVN dan HVN
    avg_vol   = volume_dist.mean()
    lvn_zones = []
    hvn_zones = []

    for i in range(bins):
        mid = (price_levels[i] + price_levels[i + 1]) / 2
        if volume_dist[i] < avg_vol * VP_CONFIG["lvn_threshold"]:
            lvn_zones.append({"price": mid, "volume": volume_dist[i]})
        elif volume_dist[i] > avg_vol * VP_CONFIG["hvn_threshold"]:
            hvn_zones.append({"price": mid, "volume": volume_dist[i]})

    return {
        "poc"          : poc_price,
        "vah"          : vah,
        "val"          : val,
        "price_max"    : price_max,
        "price_min"    : price_min,
        "lvn_zones"    : lvn_zones,
        "hvn_zones"    : hvn_zones,
        "volume_dist"  : volume_dist.tolist(),
        "price_levels" : price_levels.tolist(),
        "total_volume" : float(total_vol),
    }


def get_market_state(current_price: float,
                      vp: dict,
                      threshold_pct: float = 0.002) -> str:
    """
    Tentukan state market vs Value Area.

    OUT_HIGH = di atas VAH → potential short/trend down
    OUT_LOW  = di bawah VAL → potential long/trend up
    BALANCED = dalam Value Area → mean reversion
    """
    if not vp:
        return "BALANCED"

    vah = vp.get("vah", current_price)
    val = vp.get("val", current_price)

    upper = vah * (1 + threshold_pct)
    lower = val * (1 - threshold_pct)

    if current_price > upper:
        state = "OUT_HIGH"
    elif current_price < lower:
        state = "OUT_LOW"
    else:
        state = "BALANCED"

    return state


def find_nearest_lvn(current_price: float,
                      vp: dict,
                      direction: str) -> float:
    """Cari LVN terdekat di arah trade untuk TP."""
    lvn_zones = vp.get("lvn_zones", [])
    if not lvn_zones:
        return current_price

    if direction == "UP":
        candidates = [z["price"] for z in lvn_zones
                      if z["price"] > current_price]
        return min(candidates) if candidates else current_price
    else:
        candidates = [z["price"] for z in lvn_zones
                      if z["price"] < current_price]
        return max(candidates) if candidates else current_price


def find_nearest_hvn(current_price: float,
                      vp: dict,
                      direction: str) -> float:
    """Cari HVN terdekat di arah trade."""
    hvn_zones = vp.get("hvn_zones", [])
    if not hvn_zones:
        return current_price

    if direction == "UP":
        candidates = [z["price"] for z in hvn_zones
                      if z["price"] > current_price]
        return min(candidates) if candidates else current_price
    else:
        candidates = [z["price"] for z in hvn_zones
                      if z["price"] < current_price]
        return max(candidates) if candidates else current_price


def detect_profile_shape(vp: dict) -> str:
    """
    Deteksi bentuk VP: P-shape, b-shape, balanced.

    P-shape: volume terkonsentrasi di ATAS
    → Expect retest ke bawah lalu naik

    b-shape: volume terkonsentrasi di BAWAH
    → Expect retest ke atas lalu turun

    balanced: volume merata
    """
    if not vp:
        return "balanced"

    vol_dist = vp.get("volume_dist", [])
    if not vol_dist:
        return "balanced"

    mid       = len(vol_dist) // 2
    upper_vol = sum(vol_dist[mid:])
    lower_vol = sum(vol_dist[:mid])

    if upper_vol > lower_vol * 1.5:
        return "P-shape"
    elif lower_vol > upper_vol * 1.5:
        return "b-shape"
    return "balanced"


def get_composite_vp(days: int = 3) -> dict:
    """
    Build composite VP dari beberapa hari.
    Dipakai untuk identifikasi level super kuat
    (Fabio: profile merging).

    Level yang muncul di VP 3 hari berturut-turut
    = level SANGAT kuat untuk entry.
    """
    try:
        # Ambil klines 3 hari
        limit   = days * 1440
        klines  = get_klines(interval="1m", limit=limit)
        if not klines:
            return {}
        return build_volume_profile(klines, bins=100)
    except Exception as e:
        logger.error(f"Composite VP error: {e}")
        return {}
