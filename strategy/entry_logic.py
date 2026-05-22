# ============================================================
# strategy/entry_logic.py — LAYER 4: ENTRY DECISION ENGINE
# ERIDA Trading Bot
#
# Pusat keputusan entry — semua layer harus setuju
# sebelum bot boleh masuk posisi.
#
# Urutan evaluasi:
# 1. Daily bias (boleh long/short hari ini?)
# 2. Session quality (apakah waktu yang tepat?)
# 3. Liquidity (sudah sweep atau masih menuju sweep?)
# 4. Market structure (ChoCh/BOS/OB terkonfirmasi?)
# 5. VWAP (posisi harga vs VWAP?)
# 6. Orderflow signal (absorption/delta/liquidation?)
# 7. Final scoring dan tier
# ============================================================

from loguru import logger
from config import SIGNAL_CONFIG
from engine.daily_bias import get_daily_bias
from engine.session_structure import (get_session_structure,
                                       is_breakout_from_or)
from engine.liquidity_map import (find_liquidity_pools,
                                   detect_liquidity_sweep,
                                   is_price_near_liquidity,
                                   get_liquidity_targets)
from engine.market_structure import (analyze_market_structure,
                                      get_nearest_order_block,
                                      get_nearest_fvg)
from engine.vwap_calculator import get_vwap_summary
from engine.signal_scorer import calculate_signal_score
from data.binance_rest import get_klines


def get_entry_decision(price: float,
                        vp: dict,
                        klines: list,
                        macro_bias: dict = None,
                        funding_rate: float = 0.0,
                        oi_change: float = 0.0) -> dict:
    """
    Entry decision engine utama ERIDA.
    Semua layer dievaluasi secara berurutan.
    Jika salah satu gate gagal → no trade.
    """
    no_trade = _no_trade("Initializing")

    if not vp or not klines or price == 0:
        return _no_trade("No data")

    # ── GATE 1: DAILY BIAS ───────────────────────────────────
    daily_bias = get_daily_bias()

    # NEUTRAL bias masih boleh trade jika ada sinyal kuat
    # hanya skip jika bias sangat lemah (score < -30)
    if daily_bias["skip_today"] and abs(daily_bias.get("score", 0)) > 30:
        return _no_trade(
            f"Daily bias too weak ({daily_bias['score']}) "
            f"— skip today"
        )

    # ── GATE 2: SESSION QUALITY ──────────────────────────────
    session = get_session_structure()

    # Session quality minimum 30 (turun dari 40/50)
    quality_score = session.get("session_quality", {}).get("score", 0)
    if quality_score < 30:
        return _no_trade(
            f"Session quality too low "
            f"({quality_score}/100) "
            f"— {session.get('kill_zone', {}).get('name', '')}"
        )

    # ── GATE 3: LIQUIDITY CHECK ──────────────────────────────
    klines_15m  = get_klines(interval="15m", limit=100)
    liq_map     = find_liquidity_pools(klines_15m, lookback=80)
    liq_sweep   = detect_liquidity_sweep(klines_15m, liq_map)
    near_liq    = is_price_near_liquidity(price, liq_map)

    # ── GATE 4: MARKET STRUCTURE ─────────────────────────────
    structure   = analyze_market_structure(klines_15m, lookback=60)

    # ── GATE 5: VWAP ─────────────────────────────────────────
    vwap_data   = get_vwap_summary(price)

    # ── GATE 6: SIGNAL SCORING ───────────────────────────────
    signal = calculate_signal_score(
        price      = price,
        klines_1m  = klines_15m,
        klines_15m = klines_15m,
        daily_bias = daily_bias,
        session    = session,
        vp         = vp,
    )

    # Tidak ada sinyal
    if not signal.get("long_signal") and not signal.get("short_signal"):
        return _no_trade("No signal from scorer")

    # ── TENTUKAN DIRECTION ───────────────────────────────────
    direction  = "LONG" if signal.get("long_signal") else "SHORT"
    score      = (signal["long_score"] if direction == "LONG"
                  else signal["short_score"])
    tier       = (signal["long_tier"] if direction == "LONG"
                  else signal["short_tier"])
    reasons    = (signal["long_reasons"] if direction == "LONG"
                  else signal["short_reasons"])

    # ── VALIDASI FINAL ───────────────────────────────────────
    # Cek daily bias alignment
    if direction == "LONG" and daily_bias["only_short"]:
        return _no_trade("Daily bias only SHORT")
    if direction == "SHORT" and daily_bias["only_long"]:
        return _no_trade("Daily bias only LONG")

    # Cek apakah harga near liquidity tanpa sweep
    if near_liq["should_wait"] and not liq_sweep["swept"]:
        return _no_trade("Near liquidity — wait for sweep")

    # ── LIQUIDITY TARGETS UNTUK TP ───────────────────────────
    liq_targets = get_liquidity_targets(direction, liq_map)

    # ── ORDER BLOCK UNTUK SL ─────────────────────────────────
    nearest_ob = get_nearest_order_block(price, structure, direction)

    # ── FVG UNTUK TP ─────────────────────────────────────────
    nearest_fvg = get_nearest_fvg(price, structure, direction)

    # ── SESSION INFO ─────────────────────────────────────────
    kz        = session.get("kill_zone", {})
    orb       = is_breakout_from_or(price, session)
    vwap_val  = vwap_data.get("vwap", 0)

    # ── SIZE MULTIPLIER ──────────────────────────────────────
    # Berdasarkan kualitas setup
    size_mult = _calculate_size_multiplier(
        session    = session,
        daily_bias = daily_bias,
        liq_sweep  = liq_sweep,
        orb        = orb,
        score      = score,
    )

    logger.info(
        f"✅ ENTRY DECISION | {direction} | "
        f"Score: {score} | Tier: {tier} | "
        f"Session: {kz.get('name', '')} | "
        f"Daily: {daily_bias['bias']} | "
        f"Size mult: {size_mult:.2f} | "
        f"Reasons: {', '.join(reasons[:3])}"
    )

    if not isinstance(locals().get("model", None), str):
        model = "model_a"
    return {
        "should_trade"   : True,
        "model"          : model,
        "direction"      : direction,
        "tier"           : tier,
        "score"          : score,
        "reasons"        : reasons,
        "size_mult"      : size_mult,

        # Context
        "daily_bias"     : daily_bias["bias"],
        "daily_score"    : daily_bias["score"],
        "session"        : kz.get("name", ""),
        "session_quality": session.get(
            "session_quality", {}).get("score", 0),
        "htf_trend"      : daily_bias["htf_trend"],

        # Levels
        "vwap"           : vwap_val,
        "liq_targets"    : liq_targets,
        "nearest_ob"     : nearest_ob,
        "nearest_fvg"    : nearest_fvg,
        "liq_sweep"      : liq_sweep,
        "orb"            : orb,

        # Structure
        "structure_trend": structure.get("trend", ""),
        "choch"          : structure.get("choch", {}),
        "bos"            : structure.get("bos", {}),
    }


def _calculate_size_multiplier(session: dict,
                                 daily_bias: dict,
                                 liq_sweep: dict,
                                 orb: dict,
                                 score: int) -> float:
    """
    Hitung multiplier ukuran posisi berdasarkan kualitas setup.

    Fabio: "Increase size only when ALL conditions align"
    "Reduce size when conditions are uncertain"
    """
    mult = 1.0

    # Kill zone multiplier
    kz_quality = session.get("kill_zone", {}).get("quality", 30)
    if kz_quality >= 90:
        mult *= 1.0       # Full size di NY open
    elif kz_quality >= 75:
        mult *= 0.85      # London atau power hour
    elif kz_quality >= 50:
        mult *= 0.65      # Asia
    else:
        mult *= 0.40      # Off-hours

    # Daily bias strength
    db_score = abs(daily_bias.get("score", 0))
    if db_score >= 60:
        mult *= 1.0       # Strong bias
    elif db_score >= 40:
        mult *= 0.85      # Normal bias
    else:
        mult *= 0.70      # Weak bias

    # Liquidity sweep bonus (high probability setup)
    if liq_sweep.get("swept"):
        mult *= 1.15      # Bonus untuk sweep setup

    # ORB breakout bonus
    if orb.get("breakout"):
        mult *= 1.10      # Bonus untuk ORB

    # Score based
    if score >= 10:
        mult *= 1.0
    elif score >= 7:
        mult *= 0.85
    elif score >= 5:
        mult *= 0.65
    else:
        mult *= 0.40

    # Cap antara 0.25 dan 1.0
    return round(min(max(mult, 0.25), 1.0), 2)


def _no_trade(reason: str) -> dict:
    """Return no-trade decision."""
    if reason != "Initializing":
        logger.debug(f"⏳ No trade: {reason}")
    if not isinstance(locals().get("model", None), str):
        model = "model_a"
    return {
        "should_trade" : False,
        "reason"       : reason,
        "direction"    : None,
        "tier"         : None,
        "score"        : 0,
        "size_mult"    : 0,
    }
