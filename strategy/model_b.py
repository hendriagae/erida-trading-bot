# ============================================================
# strategy/model_b.py — MEAN REVERSION MODEL
# ERIDA Trading Bot
#
# Model B: Fade ke boundary, target kembali ke fair value.
#
# Setup ideal Model B:
# 1. Market dalam kondisi balanced (ranging)
# 2. Harga di boundary VAH/VAL atau VWAP ±2SD
# 3. Ada rejection signal (absorption, wick panjang)
# 4. VWAP rejection terkonfirmasi
# 5. Target: kembali ke POC atau VWAP
#
# Fabio: "70% of the time market is stationary.
#         Model B makes money in ranging market."
# London session = prime time Model B
# ============================================================

from loguru import logger
from config import RISK_CONFIG
from engine.volume_profile import get_market_state
from engine.absorption import calculate_absorption_score
from engine.vwap_calculator import (get_vwap_summary,
                                     detect_vwap_rejection)
from engine.market_structure import analyze_market_structure
from data.binance_rest import get_klines
from data.binance_ws import load_cache


def evaluate_model_b(price: float,
                      vp: dict,
                      klines: list,
                      daily_bias: dict,
                      session: dict) -> dict:
    """
    Evaluasi setup Model B — Mean Reversion.

    Entry di extreme (VAH/VAL atau VWAP ±2SD)
    Target kembali ke fair value (POC atau VWAP)
    """
    empty = _empty_signal()

    if not vp:
        empty["skip_reason"] = "No VP data"
        return empty

    vah = vp.get("vah", price)
    val = vp.get("val", price)
    poc = vp.get("poc", price)

    market_state = get_market_state(price, vp)

    # ── STEP 1: LOCATION CHECK ───────────────────────────────
    # Model B butuh harga di extreme area
    at_vah      = price >= vah * 0.998
    at_val      = price <= val * 1.002
    out_high    = market_state == "OUT_HIGH"
    out_low     = market_state == "OUT_LOW"

    near_extreme = at_vah or at_val or out_high or out_low
    if not near_extreme:
        empty["skip_reason"] = "Price not at extreme — Model B needs boundary"
        return empty

    # Tentukan direction
    if at_vah or out_high:
        direction = "SHORT"  # fade ke atas, target turun ke POC
    else:
        direction = "LONG"   # fade ke bawah, target naik ke POC

    # ── STEP 2: VWAP ANALYSIS ────────────────────────────────
    vwap_data    = get_vwap_summary(price)
    vwap         = vwap_data.get("vwap", 0)
    vwap_levels  = vwap_data.get("levels", {})
    vwap_ana     = vwap_data.get("analysis", {})
    vwap_zone    = vwap_ana.get("zone", "")

    # VWAP rejection adalah konfirmasi terkuat untuk Model B
    vwap_reject  = {}
    if vwap > 0:
        vwap_reject = detect_vwap_rejection(klines, vwap)

    vwap_bonus = 0
    if vwap_reject.get("rejected"):
        rv_dir = vwap_reject.get("direction", "")
        if ((direction == "LONG"  and rv_dir == "BULLISH") or
                (direction == "SHORT" and rv_dir == "BEARISH")):
            vwap_bonus = 3
            logger.info(
                f"🎯 VWAP rejection confirmed for "
                f"Model B {direction}"
            )

    # VWAP zone check
    # SHORT: harga di PREMIUM zone (di atas VWAP +1SD)
    # LONG: harga di DISCOUNT zone (di bawah VWAP -1SD)
    zone_bonus = 0
    if direction == "SHORT" and "PREMIUM" in vwap_zone:
        zone_bonus = 1
    elif direction == "LONG" and "DISCOUNT" in vwap_zone:
        zone_bonus = 1

    # ── STEP 3: ABSORPTION CONFIRMATION ─────────────────────
    abs_data  = calculate_absorption_score(seconds=60)
    cache     = load_cache()
    delta_pct = cache.get("delta_pct", 0)

    abs_bonus = 0
    if direction == "LONG" and abs_data["is_bullish"]:
        abs_bonus = min(abs_data["score"], 3)
    elif direction == "SHORT" and abs_data["is_bearish"]:
        abs_bonus = min(abs_data["score"], 3)

    # Divergence check (delta berlawanan harga = reversal signal)
    div_bonus = 0
    if direction == "SHORT" and delta_pct < -20:
        # Harga di atas tapi seller dominan = bearish divergence
        div_bonus = 1
    elif direction == "LONG" and delta_pct > 20:
        # Harga di bawah tapi buyer dominan = bullish divergence
        div_bonus = 1

    # ── STEP 4: STRUCTURE CHECK ──────────────────────────────
    klines_15m = get_klines(interval="15m", limit=50)
    structure  = analyze_market_structure(klines_15m, lookback=40)
    struct_trend = structure.get("trend", "UNDEFINED")

    # Model B lebih baik saat market CONTRACTING atau UNDEFINED
    struct_bonus = 0
    if struct_trend in ["CONTRACTING", "UNDEFINED"]:
        struct_bonus = 1
    elif struct_trend == "EXPANDING":
        struct_bonus = 0
    # Jika trending kuat berlawanan = kurangi score
    elif (direction == "LONG"  and struct_trend == "DOWNTREND"):
        struct_bonus = -1
    elif (direction == "SHORT" and struct_trend == "UPTREND"):
        struct_bonus = -1

    # ── STEP 5: SESSION CHECK ────────────────────────────────
    # Model B lebih cocok di London atau ranging session
    session_dir = session.get("session_dir", "UNDEFINED")
    kz          = session.get("kill_zone", {})
    kz_name     = kz.get("name", "")

    session_bonus = 0
    if "London" in kz_name:
        session_bonus = 1  # London = prime time Model B
    elif session_dir == "INSIDE_RANGE":
        session_bonus = 1  # Ranging = good for Model B

    # ── TOTAL SCORE ──────────────────────────────────────────
    location_score = (2 if (out_high or out_low) else 1)
    total_score = (location_score + vwap_bonus + zone_bonus +
                   abs_bonus + div_bonus + struct_bonus + session_bonus)
    total_score = max(0, total_score)

    # ── ENTRY PARAMETERS ─────────────────────────────────────
    tick = price * 0.0001

    if direction == "SHORT":
        # SL: di atas VAH atau VWAP +2SD (whichever is further)
        sl_candidates = [vah * 1.002]
        upper_2sd = vwap_levels.get("upper_2sd", 0)
        if upper_2sd > 0:
            sl_candidates.append(upper_2sd)
        sl = max(sl_candidates) + (2 * tick)

        # TP: POC (fair value) lalu VWAP lalu VAL
        tp1 = poc
        tp2 = vwap if vwap > 0 and vwap < price else val
        tp3 = val

    else:  # LONG
        # SL: di bawah VAL atau VWAP -2SD
        sl_candidates = [val * 0.998]
        lower_2sd = vwap_levels.get("lower_2sd", 0)
        if lower_2sd > 0:
            sl_candidates.append(lower_2sd)
        sl = min(sl_candidates) - (2 * tick)

        # TP: POC lalu VWAP lalu VAH
        tp1 = poc
        tp2 = vwap if vwap > 0 and vwap > price else vah
        tp3 = vah

    # Validasi SL/TP
    sl_dist = abs(price - sl)
    min_dist = price * 0.002  # minimum 0.2%
    if sl_dist < min_dist:
        sl = (price - min_dist if direction == "LONG"
              else price + min_dist)
        sl_dist = min_dist

    entry_valid = total_score >= 4

    if entry_valid:
        logger.info(
            f"🎯 MODEL B | {direction} | "
            f"Score: {total_score} | "
            f"Location: {market_state} | "
            f"VWAP reject: {vwap_reject.get('rejected')} | "
            f"Abs: {abs_data['score']}"
        )

    reasons = _build_reasons(
        location_score, vwap_bonus, zone_bonus,
        abs_bonus, div_bonus, struct_bonus, session_bonus,
        market_state, vwap_reject, kz_name
    )

    return {
        "direction"    : direction,
        "score"        : total_score,
        "entry_valid"  : entry_valid,
        "skip_reason"  : "" if entry_valid else f"Score {total_score} < 4",
        "model"        : "B",
        "sl"           : round(sl, 2),
        "tp1"          : round(tp1, 2),
        "tp2"          : round(tp2, 2),
        "tp3"          : round(tp3, 2),
        "target_label" : "POC/VWAP",
        "vwap"         : vwap,
        "vwap_reject"  : vwap_reject,
        "absorption"   : abs_data,
        "market_state" : market_state,
        "reasons"      : reasons,
    }


def _build_reasons(loc, vwap_b, zone_b, abs_b,
                    div_b, struct_b, sess_b,
                    market_state, vwap_rej, kz) -> list:
    r = []
    if loc >= 2:
        r.append(f"Out-of-balance {market_state} (+{loc})")
    else:
        r.append(f"At VA boundary (+{loc})")
    if vwap_b > 0:
        r.append(f"VWAP rejection (+{vwap_b})")
    if zone_b > 0:
        r.append(f"VWAP zone (+{zone_b})")
    if abs_b > 0:
        r.append(f"Absorption (+{abs_b})")
    if div_b > 0:
        r.append(f"Delta divergence (+{div_b})")
    if sess_b > 0:
        r.append(f"Session {kz} (+{sess_b})")
    return r


def _empty_signal() -> dict:
    return {
        "direction"  : None,
        "score"      : 0,
        "entry_valid": False,
        "skip_reason": "",
        "model"      : "B",
        "sl"         : 0,
        "tp1"        : 0,
        "tp2"        : 0,
        "tp3"        : 0,
        "reasons"    : [],
    }
