# ============================================================
# strategy/position_sizer.py — LAYER 7: RISK MANAGEMENT
# ERIDA Trading Bot
#
# Hitung ukuran posisi dengan benar:
# → SL distance dari Order Block (bukan ATR arbitrary)
# → Kelly Criterion untuk optimal sizing
# → Tier multiplier
# → Session quality multiplier
# → Consecutive loss protection
# ============================================================

import math
from loguru import logger
from config import RISK_CONFIG, SIGNAL_CONFIG


def calculate_position_size(account_balance: float,
                             entry_price: float,
                             sl_price: float,
                             signal_tier: str,
                             regime: str = "ranging",
                             consecutive_losses: int = 0,
                             session_profit: float = 0.0,
                             size_mult: float = 1.0) -> dict:
    """
    Hitung ukuran posisi optimal.
    SL distance sudah dihitung dari OB/structure — lebih akurat.
    """
    if entry_price == 0 or sl_price == 0:
        return _zero_size("Invalid entry/SL")

    # ── SL DISTANCE ──────────────────────────────────────────
    sl_distance     = abs(entry_price - sl_price)
    sl_distance_pct = sl_distance / entry_price

    # Minimum SL 0.15% untuk hindari noise
    if sl_distance_pct < 0.0015:
        sl_distance     = entry_price * 0.0015
        sl_distance_pct = 0.0015
        logger.warning(
            f"SL terlalu dekat, pakai minimum 0.15%"
        )

    # Maximum SL 1.5% untuk hindari risiko berlebihan
    if sl_distance_pct > 0.015:
        sl_distance     = entry_price * 0.015
        sl_distance_pct = 0.015
        logger.warning(
            f"SL terlalu jauh, cap di 1.5%"
        )

    # ── BASE RISK ────────────────────────────────────────────
    base_risk_pct = RISK_CONFIG["risk_per_trade_pct"]

    # Fabio: jika sudah profit hari ini,
    # risk dari profit saja (protect capital)
    if session_profit > account_balance * 0.02:
        # Sudah profit > 2% hari ini
        risk_usd = min(
            account_balance * base_risk_pct,
            session_profit * 0.3
        )
        logger.debug(
            f"Risking from profit: ${risk_usd:.2f}"
        )
    else:
        risk_usd = account_balance * base_risk_pct

    # ── TIER MULTIPLIER ──────────────────────────────────────
    tier_map = {
        "Tier1": SIGNAL_CONFIG["tier1_size_mult"],
        "Tier2": SIGNAL_CONFIG["tier2_size_mult"],
        "Tier3": SIGNAL_CONFIG["tier3_size_mult"],
    }
    tier_mult = tier_map.get(signal_tier, 0.35)

    # ── STREAK PROTECTION ────────────────────────────────────
    streak_mult = 1.0
    max_losses  = RISK_CONFIG["max_consecutive_losses"]
    if consecutive_losses >= max_losses:
        # Setiap loss tambahan kurangi 30%
        extra      = consecutive_losses - max_losses
        streak_mult = max(0.2, 1.0 - (extra + 1) * 0.30)
        logger.warning(
            f"Streak protection: {consecutive_losses} losses → "
            f"size x{streak_mult:.2f}"
        )

    # ── FINAL CALCULATION ────────────────────────────────────
    adjusted_risk = risk_usd * tier_mult * streak_mult * size_mult

    # Quantity = risk / SL distance per BTC
    quantity = adjusted_risk / sl_distance

    # Round ke 3 desimal (minimum 0.001 BTC)
    quantity = round(max(quantity, 0.001), 3)

    # ── MAX RISK CHECK ───────────────────────────────────────
    leverage      = RISK_CONFIG["default_leverage"]
    actual_risk   = quantity * sl_distance
    max_risk      = account_balance * RISK_CONFIG["max_risk_per_trade_pct"]

    if actual_risk > max_risk:
        scale    = max_risk / actual_risk
        quantity = round(quantity * scale, 3)
        actual_risk = quantity * sl_distance

    margin = (quantity * entry_price) / leverage

    result = {
        "quantity"        : quantity,
        "position_usd"    : quantity * entry_price,
        "margin_needed"   : margin,
        "risk_usd"        : actual_risk,
        "risk_pct"        : actual_risk / account_balance * 100,
        "sl_distance_pct" : sl_distance_pct * 100,
        "tier_mult"       : tier_mult,
        "streak_mult"     : streak_mult,
        "size_mult"       : size_mult,
        "leverage"        : leverage,
    }

    logger.info(
        f"📦 Size: {quantity:.3f} BTC | "
        f"Risk: ${actual_risk:.2f} "
        f"({result['risk_pct']:.2f}%) | "
        f"SL: {sl_distance_pct*100:.3f}% | "
        f"Tier: x{tier_mult} | "
        f"Streak: x{streak_mult}"
    )

    return result


def calculate_sl_tp_from_structure(direction: str,
                                    entry_price: float,
                                    model_signal: dict,
                                    vp: dict) -> dict:
    """
    Ambil SL/TP dari model signal (sudah dihitung di model_a/b).
    Validasi dan pastikan masuk akal.

    Ini BERBEDA dari versi lama yang pakai ATR arbitrary.
    Sekarang SL diletakkan di Order Block — jauh lebih akurat.
    """
    sl  = model_signal.get("sl", 0)
    tp1 = model_signal.get("tp1", 0)
    tp2 = model_signal.get("tp2", 0)
    tp3 = model_signal.get("tp3", 0)

    # Validasi SL
    if sl == 0:
        # Fallback: 0.3% dari entry
        sl = (entry_price * (1 - 0.003) if direction == "LONG"
              else entry_price * (1 + 0.003))

    sl_dist = abs(entry_price - sl)
    min_dist = entry_price * 0.0015
    max_dist = entry_price * 0.015

    if sl_dist < min_dist:
        sl = (entry_price - min_dist if direction == "LONG"
              else entry_price + min_dist)
        sl_dist = min_dist

    if sl_dist > max_dist:
        sl = (entry_price - max_dist if direction == "LONG"
              else entry_price + max_dist)
        sl_dist = max_dist

    # Validasi SL arah — CRITICAL FIX
    # SL LONG harus DI BAWAH entry
    # SL SHORT harus DI ATAS entry
    if direction == "LONG" and sl >= entry_price:
        sl      = entry_price * (1 - 0.003)
        sl_dist = abs(entry_price - sl)
    elif direction == "SHORT" and sl <= entry_price:
        sl      = entry_price * (1 + 0.003)
        sl_dist = abs(entry_price - sl)

    # Validasi TP
    # TP harus lebih jauh dari entry dari SL
    if direction == "LONG":
        if tp1 <= entry_price:
            tp1 = entry_price + sl_dist * 1.0
        if tp2 <= tp1:
            tp2 = entry_price + sl_dist * 2.0
        if tp3 <= tp2:
            tp3 = entry_price + sl_dist * 3.5
    else:
        if tp1 >= entry_price:
            tp1 = entry_price - sl_dist * 1.0
        if tp2 >= tp1:
            tp2 = entry_price - sl_dist * 2.0
        if tp3 >= tp2:
            tp3 = entry_price - sl_dist * 3.5

    result = {
        "sl"      : round(sl, 2),
        "tp1"     : round(tp1, 2),
        "tp2"     : round(tp2, 2),
        "tp3"     : round(tp3, 2),
        "sl_dist" : round(sl_dist, 2),
        "rr1"     : round(abs(tp1 - entry_price) / sl_dist, 2),
        "rr2"     : round(abs(tp2 - entry_price) / sl_dist, 2),
        "rr3"     : round(abs(tp3 - entry_price) / sl_dist, 2),
    }

    logger.info(
        f"📐 {direction} | "
        f"SL: ${sl:,.2f} ({sl_dist:.0f}) | "
        f"TP1: ${tp1:,.2f} (RR {result['rr1']}) | "
        f"TP2: ${tp2:,.2f} (RR {result['rr2']})"
    )

    return result


def calculate_atr(klines: list, period: int = 14) -> float:
    """ATR dengan minimum 0.3% dari harga."""
    if len(klines) < period + 1:
        return klines[-1]["close"] * 0.003 if klines else 100.0
    trs = []
    for i in range(1, len(klines)):
        h, l, pc = klines[i]["high"], klines[i]["low"], klines[i-1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(trs[-period:]) / period
    min_atr = klines[-1]["close"] * 0.003
    return max(atr, min_atr)


def _zero_size(reason: str) -> dict:
    logger.warning(f"Size = 0: {reason}")
    return {
        "quantity": 0, "position_usd": 0,
        "margin_needed": 0, "risk_usd": 0,
        "risk_pct": 0, "reason": reason,
    }
