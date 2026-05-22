# ============================================================
# engine/signal_scorer.py — LAYER 4: SIGNAL SCORING
# ERIDA Trading Bot v3.1
#
# UPDATE v3.1 berdasarkan data 26 trade:
# 1. Score minimum naik: Tier1=8, Tier2=6
# 2. Momentum filter: skip jika >0.5% dalam 5 menit
# 3. Konflik sinyal: skip jika ada sinyal berlawanan <5 menit
# 4. CHoCH sebagai hard requirement (bukan bonus)
# 5. Minimum RR 1.5:1 divalidasi di sini
# ============================================================

import time
from loguru import logger
from engine.market_structure import (analyze_market_structure,
                                      get_nearest_order_block,
                                      get_nearest_fvg)
from engine.liquidity_map    import (find_liquidity_pools,
                                      detect_liquidity_sweep,
                                      is_price_near_liquidity)
from engine.vwap_calculator  import get_vwap_summary
from engine.absorption       import calculate_absorption_score
from data.binance_ws         import load_cache
from config                  import SIGNAL_CONFIG

# ── STATE untuk konflik sinyal dan momentum ──────────────────
_signal_history = {
    "last_long_time"  : 0,
    "last_short_time" : 0,
    "last_direction"  : None,
}

# Simpan history harga untuk momentum filter
_price_history = []


def update_price_history(price: float):
    """Simpan history harga setiap cycle."""
    global _price_history
    _price_history.append({"price": price, "time": time.time()})
    # Simpan hanya 10 menit terakhir
    cutoff = time.time() - 600
    _price_history = [p for p in _price_history if p["time"] > cutoff]


def _check_momentum_filter(direction: str) -> dict:
    """
    Filter momentum: skip jika harga bergerak terlalu cepat.
    Yosh: "Don't get in front of a moving train"

    Jika harga naik >0.5% dalam 5 menit = skip LONG
    Jika harga turun >0.5% dalam 5 menit = skip SHORT
    """
    if len(_price_history) < 3:
        return {"blocked": False, "reason": ""}

    now     = time.time()
    cutoff  = now - 300  # 5 menit
    recent  = [p for p in _price_history if p["time"] > cutoff]

    if len(recent) < 2:
        return {"blocked": False, "reason": ""}

    oldest  = recent[0]["price"]
    current = recent[-1]["price"]
    move    = (current - oldest) / oldest

    threshold = 0.005  # 0.5%

    if direction == "LONG" and move > threshold:
        reason = (f"Momentum terlalu kuat BULLISH "
                  f"({move*100:.2f}% dalam 5 menit) — "
                  f"tunggu retracement")
        logger.warning(f"⚡ Momentum filter: {reason}")
        return {"blocked": True, "reason": reason}

    if direction == "SHORT" and move < -threshold:
        reason = (f"Momentum terlalu kuat BEARISH "
                  f"({move*100:.2f}% dalam 5 menit) — "
                  f"tunggu retracement")
        logger.warning(f"⚡ Momentum filter: {reason}")
        return {"blocked": True, "reason": reason}

    return {"blocked": False, "reason": ""}


def _check_signal_conflict(direction: str) -> dict:
    """
    Filter konflik sinyal: skip jika ada sinyal berlawanan
    dalam 5 menit terakhir.

    Mencegah bot bolak-balik LONG/SHORT dalam hitungan menit.
    """
    now             = time.time()
    conflict_window = 300  # 5 menit

    if direction == "LONG":
        last_opposite = _signal_history["last_short_time"]
    else:
        last_opposite = _signal_history["last_long_time"]

    if now - last_opposite < conflict_window:
        sisa = int(conflict_window - (now - last_opposite))
        reason = (f"Ada sinyal {('SHORT' if direction == 'LONG' else 'LONG')} "
                  f"{int((now - last_opposite)/60)} menit lalu — "
                  f"konflik arah, skip ({sisa}s lagi)")
        logger.warning(f"⚔️ Conflict filter: {reason}")
        return {"blocked": True, "reason": reason}

    return {"blocked": False, "reason": ""}


def _validate_rr(direction: str, price: float,
                  sl: float, tp1: float,
                  min_rr: float = 1.5) -> dict:
    """
    Validasi Risk-Reward sebelum entry.
    Chris Drysdale: "Math either says yes or trade doesn't happen"
    """
    if sl == 0 or tp1 == 0:
        return {"valid": True, "rr": 0}

    sl_dist  = abs(price - sl)
    tp1_dist = abs(tp1 - price)

    if sl_dist == 0:
        return {"valid": True, "rr": 0}

    rr = tp1_dist / sl_dist

    if rr < min_rr:
        logger.warning(
            f"📉 RR {rr:.2f} < minimum {min_rr} — skip trade | "
            f"SL: {sl_dist:.0f} pts | TP1: {tp1_dist:.0f} pts"
        )
        return {"valid": False, "rr": round(rr, 2)}

    return {"valid": True, "rr": round(rr, 2)}


def calculate_signal_score(
    price     : float,
    klines_1m : list,
    klines_15m: list,
    daily_bias: dict,
    session   : dict,
    vp        : dict,
) -> dict:
    """
    Hitung score sinyal dengan semua filter aktif.

    UPDATE v3.1:
    - Tier1 minimum = 8 (dari 7)
    - Tier2 minimum = 6 (dari 5)
    - Momentum filter aktif
    - Konflik sinyal filter aktif
    - CHoCH lebih diberatkan
    """
    # Update price history untuk momentum filter
    update_price_history(price)

    empty = {
        "long_signal"   : False,
        "short_signal"  : False,
        "long_score"    : 0,
        "short_score"   : 0,
        "long_tier"     : None,
        "short_tier"    : None,
        "long_reasons"  : [],
        "short_reasons" : [],
        "blocked"       : False,
        "block_reason"  : "",
    }

    try:
        # ── MARKET STRUCTURE ─────────────────────────────────
        structure  = analyze_market_structure(klines_15m, 60)
        liq_map    = find_liquidity_pools(klines_15m, 80)
        liq_sweep  = detect_liquidity_sweep(klines_15m, liq_map)
        near_liq   = is_price_near_liquidity(price, liq_map)
        vwap_data  = get_vwap_summary(price)
        abs_data   = calculate_absorption_score(seconds=60)
        cache      = load_cache()
        delta_pct  = cache.get("delta_pct", 0)

        bias       = daily_bias.get("bias", "NEUTRAL")
        htf_trend  = daily_bias.get("htf_trend", "FLAT")
        bias_score = daily_bias.get("score", 0)

        kz         = session.get("kill_zone", {})
        kz_name    = kz.get("name", "")
        kz_quality = kz.get("quality", 0)

        structure_trend = structure.get("trend", "NEUTRAL")
        choch           = structure.get("choch", {})
        bos             = structure.get("bos", {})
        obs             = structure.get("order_blocks", [])
        fvgs            = structure.get("fvgs", [])

        vwap        = vwap_data.get("vwap", 0)
        vwap_ana    = vwap_data.get("analysis", {})
        vah         = vp.get("vah", 0)
        val         = vp.get("val", 0)
        poc         = vp.get("poc", 0)

        long_score   = 0
        short_score  = 0
        long_reasons  = []
        short_reasons = []

        # ── GATE 1: DAILY BIAS ───────────────────────────────
        # Strong bias = +3, Weak bias = +1, Counter = -3
        if "BULL" in bias:
            bonus = 3 if "STRONG" in bias or abs(bias_score) >= 60 else 1
            long_score  += bonus
            long_reasons.append(
                f"{'Strong' if bonus == 3 else 'Weak'} bull bias (+{bonus})"
            )
            if "STRONG" in bias:
                short_score -= 2  # Penalize short saat strong bull
        elif "BEAR" in bias:
            bonus = 3 if "STRONG" in bias or abs(bias_score) >= 60 else 1
            short_score  += bonus
            short_reasons.append(
                f"{'Strong' if bonus == 3 else 'Weak'} bear bias (+{bonus})"
            )
            if "STRONG" in bias:
                long_score -= 2  # Penalize long saat strong bear

        # ── GATE 2: MARKET STRUCTURE ─────────────────────────
        # CHoCH = tanda perubahan arah = +2
        # BOS = konfirmasi lanjut arah = +2
        choch_dir = choch.get("direction", "")
        bos_dir   = bos.get("direction", "")

        has_long_choch  = choch_dir == "BULLISH"
        has_short_choch = choch_dir == "BEARISH"
        has_long_bos    = bos_dir   == "BULLISH"
        has_short_bos   = bos_dir   == "BEARISH"

        if has_long_choch:
            long_score  += 2
            long_reasons.append("ChoCh bullish (+2)")
        if has_long_bos:
            long_score  += 2
            long_reasons.append("BOS bullish (+2)")
        if has_short_choch:
            short_score += 2
            short_reasons.append("ChoCh bearish (+2)")
        if has_short_bos:
            short_score += 2
            short_reasons.append("BOS bearish (+2)")

        # ── GATE 3: ORDER BLOCK ──────────────────────────────
        nearest_ob_long  = get_nearest_order_block(
            price, structure, "LONG"
        )
        nearest_ob_short = get_nearest_order_block(
            price, structure, "SHORT"
        )

        at_long_ob  = False
        at_short_ob = False

        if nearest_ob_long:
            dist = abs(price - nearest_ob_long.get("high", price)) / price
            if dist < 0.005:
                at_long_ob   = True
                long_score  += 2
                long_reasons.append("At bullish OB (+2)")

        if nearest_ob_short:
            dist = abs(price - nearest_ob_short.get("low", price)) / price
            if dist < 0.005:
                at_short_ob   = True
                short_score  += 2
                short_reasons.append("At bearish OB (+2)")

        # FVG
        nearest_fvg_long  = get_nearest_fvg(price, structure, "LONG")
        nearest_fvg_short = get_nearest_fvg(price, structure, "SHORT")

        if nearest_fvg_long:
            mp   = nearest_fvg_long.get("midpoint", 0)
            dist = abs(price - mp) / price if mp > 0 else 1
            if dist < 0.003:
                long_score  += 1
                long_reasons.append("At bullish FVG (+1)")

        if nearest_fvg_short:
            mp   = nearest_fvg_short.get("midpoint", 0)
            dist = abs(price - mp) / price if mp > 0 else 1
            if dist < 0.003:
                short_score += 1
                short_reasons.append("At bearish FVG (+1)")

        # ── GATE 4: LIQUIDITY ────────────────────────────────
        if liq_sweep.get("swept"):
            sweep_dir = liq_sweep.get("direction", "")
            if sweep_dir == "BULLISH":
                long_score  += 3
                long_reasons.append("Liq sweep BULLISH (+3)")
            elif sweep_dir == "BEARISH":
                short_score += 3
                short_reasons.append("Liq sweep BEARISH (+3)")

        # Volume profile edges
        if vah > 0 and abs(price - vah) / price < 0.002:
            short_score += 1
            short_reasons.append("At VAH (+1)")
        if val > 0 and abs(price - val) / price < 0.002:
            long_score  += 1
            long_reasons.append("At VAL (+1)")

        # Out of balance
        if vah > 0 and val > 0:
            if price > vah * 1.002:
                short_score += 2
                short_reasons.append("Out-of-balance HIGH (+2)")
            elif price < val * 0.998:
                long_score  += 2
                long_reasons.append("Out-of-balance LOW (+2)")

        # ── GATE 5: ORDERFLOW ────────────────────────────────
        # Absorption
        if abs_data.get("is_bullish") and abs_data.get("score", 0) >= 5:
            pts = min(abs_data["score"], 3)
            long_score  += pts
            long_reasons.append(f"Bull absorption (+{pts})")
        if abs_data.get("is_bearish") and abs_data.get("score", 0) >= 5:
            pts = min(abs_data["score"], 3)
            short_score += pts
            short_reasons.append(f"Bear absorption (+{pts})")

        # Delta
        if delta_pct > 30:
            long_score  += 2 if delta_pct > 60 else 1
            long_reasons.append(
                f"{'Strong ' if delta_pct > 60 else ''}Buy delta (+{2 if delta_pct > 60 else 1})"
            )
        if delta_pct < -30:
            short_score += 2 if delta_pct < -60 else 1
            short_reasons.append(
                f"{'Strong ' if delta_pct < -60 else ''}Sell delta (+{2 if delta_pct < -60 else 1})"
            )

        # Liquidations
        liq_long_usd  = cache.get("liq_long_usd", 0)
        liq_short_usd = cache.get("liq_short_usd", 0)

        if liq_short_usd > 500_000:
            long_score  += 2
            long_reasons.append(
                f"Short squeeze ${liq_short_usd/1e6:.1f}M (+2)"
            )
        if liq_long_usd > 500_000:
            short_score += 2
            short_reasons.append(
                f"Long squeeze ${liq_long_usd/1e6:.1f}M (+2)"
            )

        # ── GATE 6: TIMING ───────────────────────────────────
        if kz_quality >= 85:
            long_score  += 2
            short_score += 2
            long_reasons.append(f"Kill zone {kz_name} (+2)")
            short_reasons.append(f"Kill zone {kz_name} (+2)")
        elif kz_quality >= 60:
            long_score  += 1
            short_score += 1
            long_reasons.append(f"Kill zone {kz_name} (+1)")
            short_reasons.append(f"Kill zone {kz_name} (+1)")

        # VWAP alignment
        if vwap > 0:
            if price > vwap * 1.001:
                long_score  += 1
                long_reasons.append("Above VWAP (+1)")
            elif price < vwap * 0.999:
                short_score += 1
                short_reasons.append("Below VWAP (+1)")

        # ── TIER CALCULATION v3.1 ────────────────────────────
        # Tier1 = 8+ (dari 7)
        # Tier2 = 6+ (dari 5)
        TIER1_MIN = 8
        TIER2_MIN = 6

        def get_tier(score):
            if score >= TIER1_MIN:
                return "Tier1"
            elif score >= TIER2_MIN:
                return "Tier2"
            return None

        long_tier  = get_tier(long_score)
        short_tier = get_tier(short_score)

        # ── GENERATE SIGNAL ──────────────────────────────────
        # Ambil arah dengan score tertinggi
        long_signal  = long_tier  is not None
        short_signal = short_tier is not None

        # Jika keduanya ada sinyal, ambil yang lebih tinggi
        if long_signal and short_signal:
            if long_score >= short_score:
                short_signal = False
                short_tier   = None
            else:
                long_signal = False
                long_tier   = None

        # Update signal history
        if long_signal:
            _signal_history["last_long_time"]  = time.time()
            _signal_history["last_direction"]  = "LONG"
        if short_signal:
            _signal_history["last_short_time"] = time.time()
            _signal_history["last_direction"]  = "SHORT"

        result = {
            "long_signal"   : long_signal,
            "short_signal"  : short_signal,
            "long_score"    : long_score,
            "short_score"   : short_score,
            "long_tier"     : long_tier,
            "short_tier"    : short_tier,
            "long_reasons"  : long_reasons,
            "short_reasons" : short_reasons,
            "structure"     : structure,
            "liq_map"       : liq_map,
            "liq_sweep"     : liq_sweep,
            "near_liq"      : near_liq,
            "vwap_data"     : vwap_data,
            "abs_data"      : abs_data,
            "at_long_ob"    : at_long_ob,
            "at_short_ob"   : at_short_ob,
            "blocked"       : False,
            "block_reason"  : "",
        }

        # ── FILTER: MOMENTUM ─────────────────────────────────
        direction = "LONG" if long_signal else ("SHORT" if short_signal else None)
        if direction:
            momentum = _check_momentum_filter(direction)
            if momentum["blocked"]:
                result["long_signal"]  = False
                result["short_signal"] = False
                result["blocked"]      = True
                result["block_reason"] = momentum["reason"]
                return result

        # ── FILTER: KONFLIK SINYAL ───────────────────────────
        if direction:
            conflict = _check_signal_conflict(direction)
            if conflict["blocked"]:
                result["long_signal"]  = False
                result["short_signal"] = False
                result["blocked"]      = True
                result["block_reason"] = conflict["reason"]
                return result

        # Log
        if long_signal:
            logger.info(
                f"🟢 LONG | Score: {long_score}/{TIER1_MIN}+ | "
                f"Tier: {long_tier} | Bias: {bias} | "
                f"KZ: {kz_name} | "
                f"{', '.join(long_reasons[:4])}"
            )
        if short_signal:
            logger.info(
                f"🔴 SHORT | Score: {short_score}/{TIER1_MIN}+ | "
                f"Tier: {short_tier} | Bias: {bias} | "
                f"KZ: {kz_name} | "
                f"{', '.join(short_reasons[:4])}"
            )

        return result

    except Exception as e:
        logger.error(f"Signal scorer error: {e}")
        return empty


def validate_rr_before_entry(direction: str, price: float,
                               sl: float, tp1: float) -> dict:
    """
    Validasi RR sebelum execute entry.
    Dipanggil dari main.py sebelum place_market_order.
    """
    return _validate_rr(direction, price, sl, tp1, min_rr=1.5)
