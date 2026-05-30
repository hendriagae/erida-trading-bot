# ============================================================
# strategy/model_a.py — TREND / MOMENTUM MODEL
# ERIDA Trading Bot
#
# Model A: Trade searah trend setelah liquidity sweep.
#
# Setup ideal Model A:
# 1. Daily bias jelas (BULL atau BEAR)
# 2. Session direction terkonfirmasi
# 3. Liquidity sweep terjadi (stop run)
# 4. BOS (Break of Structure) terkonfirmasi
# 5. Retracement ke Order Block atau FVG
# 6. Entry saat ada big order absorption di OB/FVG
# 7. Target: liquidity pool berikutnya
#
# Fabio: "Model A adalah setup favoritku di NY session"
# "80% of the time when liquidity is swept,
#  price continues in the opposite direction"
# ============================================================

from loguru import logger
from config import FABIO_CONFIG, RISK_CONFIG
from engine.liquidity_map import (detect_liquidity_sweep,
                                   get_liquidity_targets)
from engine.market_structure import (get_nearest_order_block,
                                      get_nearest_fvg)
from engine.absorption import calculate_absorption_score
from engine.vwap_calculator import get_vwap_summary
from data.binance_rest import get_klines
from data.binance_ws import load_cache
def evaluate_model_a(price: float,
                      vp: dict,
                      klines: list,
                      daily_bias: dict,
                      session: dict,
                      structure: dict,
                      liq_map: dict,
                      direction_hint: str = None) -> dict:
    """
    Evaluasi setup Model A — Trend/Momentum.

    Return signal dengan entry level, SL, TP targets,
    dan confidence score.
    """
    empty = _empty_signal()

    # ── STEP 1: DIRECTION ────────────────────────────────────
    # Model A hanya trade searah daily bias yang kuat
    bias      = daily_bias.get("bias", "NEUTRAL")
    htf_trend = daily_bias.get("htf_trend", "FLAT")
    # Prioritaskan direction_hint dari entry decision (signal scorer)
    # Model A validasi setup, bukan tentukan arah
    if direction_hint:
        direction = direction_hint
    else:
        direction = ("LONG"  if bias in ["BULL", "WEAK_BULL"] else
                     "SHORT" if bias in ["BEAR", "WEAK_BEAR"] else
                     "LONG"  if htf_trend == "UP" else
                     "SHORT" if htf_trend == "DOWN" else None)
    if not direction:
        empty["skip_reason"] = "Unclear direction"
        return empty

    # ── STEP 2: LIQUIDITY SWEEP CONFIRMATION ─────────────────
    # Model A entry TERBAIK adalah setelah liquidity sweep
    klines_15m = get_klines(interval="15m", limit=100)
    liq_sweep  = detect_liquidity_sweep(klines_15m, liq_map)

    sweep_bonus = 0
    if liq_sweep["swept"]:
        sweep_dir = liq_sweep["direction"]
        if ((direction == "LONG"  and sweep_dir == "BULLISH") or
                (direction == "SHORT" and sweep_dir == "BEARISH")):
            sweep_bonus = 3
            logger.info(
                f"💧 Liquidity sweep confirmed for "
                f"Model A {direction}"
            )

    # ── STEP 3: ORDER BLOCK LOCATION ─────────────────────────
    ob = get_nearest_order_block(price, structure, direction)
    fvg = get_nearest_fvg(price, structure, direction)

    at_ob  = False
    at_fvg = False
    ob_bonus = 0

    if ob:
        dist = abs(price - (ob["high"] if direction == "LONG"
                            else ob["low"])) / price
        if dist < 0.005:  # dalam 0.5%
            at_ob    = True
            ob_bonus = 2

    if fvg:
        midpoint = fvg.get("midpoint", 0)
        if midpoint > 0:
            dist = abs(price - midpoint) / price
            if dist < 0.003:
                at_fvg   = True
                ob_bonus = max(ob_bonus, 1)

    # ── STEP 4: ORDERFLOW CONFIRMATION ───────────────────────
    abs_data  = calculate_absorption_score(seconds=60)
    cache     = load_cache()
    delta_pct = cache.get("delta_pct", 0)

    of_bonus = 0
    if direction == "LONG":
        if abs_data["is_bullish"] and abs_data["score"] >= 3:
            of_bonus += min(abs_data["score"], 3)
        if delta_pct > 20:
            of_bonus += 1
    else:
        if abs_data["is_bearish"] and abs_data["score"] >= 3:
            of_bonus += min(abs_data["score"], 3)
        if delta_pct < -20:
            of_bonus += 1

    # ── STEP 5: VWAP ALIGNMENT ───────────────────────────────
    vwap_data   = get_vwap_summary(price)
    vwap        = vwap_data.get("vwap", 0)
    vwap_ana    = vwap_data.get("analysis", {})
    vwap_bias   = vwap_ana.get("bias", "NEUTRAL")

    vwap_bonus  = 0
    if direction == "LONG" and "BULL" in vwap_bias:
        vwap_bonus = 1
    elif direction == "SHORT" and "BEAR" in vwap_bias:
        vwap_bonus = 1

    # ── TOTAL SCORE ──────────────────────────────────────────
    # HTF alignment base score
    htf_score = (2 if htf_trend in ["UP", "DOWN"] else 1)

    total_score = htf_score + sweep_bonus + ob_bonus + of_bonus + vwap_bonus
    total_score = max(0, total_score)

    # ── ENTRY PARAMETERS ─────────────────────────────────────
    # SL: di luar Order Block (Fabio: 1-2 ticks sebelum level)
    tick = price * 0.0001
    if direction == "LONG":
        if ob:
            sl = ob["low"] - (2 * tick)
        else:
            sl = price * (1 - 0.003)  # 0.3% default
    else:
        if ob:
            sl = ob["high"] + (2 * tick)
        else:
            sl = price * (1 + 0.003)

    # TP: liquidity pools (target smart money sebenarnya)
    liq_targets = get_liquidity_targets(direction, liq_map)
    tp1 = liq_targets[0] if len(liq_targets) > 0 else 0
    tp2 = liq_targets[1] if len(liq_targets) > 1 else 0
    tp3 = liq_targets[2] if len(liq_targets) > 2 else 0

    # Fallback TP jika tidak ada liquidity target
    sl_dist = abs(price - sl)
    if sl_dist > 0:
        if tp1 == 0:
            tp1 = (price + sl_dist * RISK_CONFIG["rr_ratio"] * 0.5
                   if direction == "LONG"
                   else price - sl_dist * RISK_CONFIG["rr_ratio"] * 0.5)
        if tp2 == 0:
            tp2 = (price + sl_dist * RISK_CONFIG["rr_ratio"]
                   if direction == "LONG"
                   else price - sl_dist * RISK_CONFIG["rr_ratio"])
        if tp3 == 0:
            tp3 = (price + sl_dist * RISK_CONFIG["rr_ratio"] * 2
                   if direction == "LONG"
                   else price - sl_dist * RISK_CONFIG["rr_ratio"] * 2)

    entry_valid = total_score >= 4

    if entry_valid:
        logger.info(
            f"🎯 MODEL A | {direction} | "
            f"Score: {total_score} | "
            f"Sweep: {liq_sweep.get('swept')} | "
            f"At OB: {at_ob} | "
            f"Absorption: {abs_data['score']}"
        )

    return {
        "direction"    : direction,
        "score"        : total_score,
        "entry_valid"  : entry_valid,
        "skip_reason"  : "" if entry_valid else f"Score too low: {total_score}",
        "model"        : "A",
        "sl"           : round(sl, 2),
        "tp1"          : round(tp1, 2),
        "tp2"          : round(tp2, 2),
        "tp3"          : round(tp3, 2),
        "at_ob"        : at_ob,
        "at_fvg"       : at_fvg,
        "liq_sweep"    : liq_sweep,
        "absorption"   : abs_data,
        "vwap"         : vwap,
        "liq_targets"  : liq_targets,
        "ob_data"      : ob,
        "reasons"      : _build_reasons(
            htf_score, sweep_bonus, ob_bonus,
            of_bonus, vwap_bonus,
            at_ob, at_fvg, liq_sweep
        ),
    }
def _build_reasons(htf, sweep, ob, of, vwap,
                    at_ob, at_fvg, liq_sweep) -> list:
    r = []
    if htf >= 2:
        r.append(f"HTF aligned (+{htf})")
    if sweep > 0:
        r.append(f"Liquidity swept (+{sweep})")
    if ob > 0 and at_ob:
        r.append(f"At Order Block (+{ob})")
    if ob > 0 and at_fvg:
        r.append(f"At FVG (+{ob})")
    if of > 0:
        r.append(f"Orderflow (+{of})")
    if vwap > 0:
        r.append(f"VWAP aligned (+{vwap})")
    return r
def _empty_signal() -> dict:
    return {
        "direction"  : None,
        "score"      : 0,
        "entry_valid": False,
        "skip_reason": "",
        "model"      : "A",
        "sl"         : 0,
        "tp1"        : 0,
        "tp2"        : 0,
        "tp3"        : 0,
        "reasons"    : [],
    }
