# ============================================================
# engine/market_state.py
# Deteksi state pasar: balanced, out-of-balance, trending, ranging
# Konsep utama Fabio: "ONLY trade when out of balance"
# ============================================================

from dataclasses import dataclass
from loguru import logger
from config import FABIO_CONFIG, REGIME_CONFIG


@dataclass
class MarketState:
    """Representasi lengkap state pasar saat ini."""
    # Posisi vs Value Area
    location      : str    # OUT_HIGH, OUT_LOW, BALANCED
    profile_shape : str    # P-shape, b-shape, balanced

    # Regime
    regime        : str    # trending_strong, trending_normal,
                           # ranging, high_volatility, crisis

    # Trend direction
    trend         : str    # UP, DOWN, FLAT

    # Model yang dipakai
    recommended_model : str  # model_a (trend) atau model_b (reversion)

    # Context
    session       : str    # london, new_york, asian, off_hours
    is_tradeable  : bool   # apakah kondisi layak untuk trade
    reason        : str    # alasan is_tradeable


def determine_market_state(price: float,
                            vp: dict,
                            klines: list,
                            session: str,
                            volatility_mult: float = 1.0) -> MarketState:
    """
    Tentukan state pasar secara komprehensif.

    Gabungan:
    - Fabio's 3-step: location → level → aggression
    - Andrea's regime detection
    - Session-based model selection
    """
    if not vp or not klines:
        return MarketState(
            location="BALANCED", profile_shape="balanced",
            regime="ranging", trend="FLAT",
            recommended_model="model_b",
            session=session, is_tradeable=False,
            reason="Insufficient data",
        )

    vah = vp.get("vah", price)
    val = vp.get("val", price)
    poc = vp.get("poc", price)
    threshold = FABIO_CONFIG["oob_threshold_pct"]

    # ── STEP 1: LOCATION ────────────────────────────────────
    if price > vah * (1 + threshold):
        location = "OUT_HIGH"
    elif price < val * (1 - threshold):
        location = "OUT_LOW"
    else:
        location = "BALANCED"

    # ── PROFILE SHAPE ────────────────────────────────────────
    from engine.volume_profile import detect_profile_shape
    profile_shape = detect_profile_shape(vp)

    # ── TREND ────────────────────────────────────────────────
    trend = _detect_trend(klines)

    # ── REGIME ───────────────────────────────────────────────
    regime = _detect_regime(klines, volatility_mult)

    # ── MODEL RECOMMENDATION ─────────────────────────────────
    # Fabio: Model A (trend) = New York + out of balance
    #        Model B (reversion) = London + balanced / ranging
    if location != "BALANCED" and session == "new_york":
        recommended_model = "model_a"
    elif location == "BALANCED" or session == "london":
        recommended_model = "model_b"
    elif regime in ["trending_strong", "trending_normal"]:
        recommended_model = "model_a"
    else:
        recommended_model = "model_b"

    # ── TRADEABILITY ─────────────────────────────────────────
    is_tradeable = True
    reason       = "OK"

    if regime == "crisis":
        is_tradeable = False
        reason = "Crisis/extreme volatility — skip"
    elif session == "off_hours":
        is_tradeable = False
        reason = "Off-hours session — low liquidity"
    elif regime == "high_volatility" and location == "BALANCED":
        is_tradeable = False
        reason = "High volatility inside range — dangerous"

    state = MarketState(
        location          = location,
        profile_shape     = profile_shape,
        regime            = regime,
        trend             = trend,
        recommended_model = recommended_model,
        session           = session,
        is_tradeable      = is_tradeable,
        reason            = reason,
    )

    logger.debug(
        f"Market State | Location: {location} | "
        f"Regime: {regime} | Trend: {trend} | "
        f"Model: {recommended_model} | "
        f"Tradeable: {is_tradeable}"
    )

    return state


def get_current_session() -> str:
    """
    Tentukan sesi trading saat ini berdasarkan UTC time.
    Fabio: New York session = best untuk Model A
           London session   = best untuk Model B
    """
    from datetime import datetime, timezone
    now  = datetime.now(timezone.utc)
    hour = now.hour

    if 7 <= hour < 16:
        return "london"
    elif 13 <= hour < 20:
        return "new_york"     # overlap london-NY = paling liquid
    elif 0 <= hour < 7:
        return "asian"
    else:
        return "off_hours"


def get_regime_size_multiplier(regime: str) -> float:
    """Return size multiplier berdasarkan regime."""
    mapping = {
        "trending_strong"  : REGIME_CONFIG["trending_strong"],
        "trending_normal"  : REGIME_CONFIG["trending_normal"],
        "ranging"          : REGIME_CONFIG["ranging"],
        "high_volatility"  : REGIME_CONFIG["high_volatility"],
        "crisis"           : REGIME_CONFIG["crisis"],
    }
    return mapping.get(regime, 0.5)


# ── PRIVATE HELPERS ──────────────────────────────────────────

def _detect_trend(klines: list, fast: int = 10, slow: int = 30) -> str:
    """Deteksi trend dari dual EMA."""
    if len(klines) < slow:
        return "FLAT"
    closes    = [k["close"] for k in klines]
    ema_fast  = _ema(closes, fast)
    ema_slow  = _ema(closes, slow)
    gap       = (ema_fast - ema_slow) / ema_slow

    if gap > 0.002:
        return "UP"
    elif gap < -0.002:
        return "DOWN"
    return "FLAT"


def _detect_regime(klines: list, vol_mult: float = 1.0) -> str:
    """
    Deteksi regime dari volatility dan directional movement.
    """
    if len(klines) < 20:
        return "ranging"

    closes  = [k["close"] for k in klines[-20:]]
    volumes = [k["volume"] for k in klines[-20:]]

    # Volatility: std dev of returns
    returns = [(closes[i] - closes[i-1]) / closes[i-1]
               for i in range(1, len(closes))]
    avg_return  = sum(abs(r) for r in returns) / len(returns)
    avg_vol     = sum(volumes) / len(volumes)
    latest_vol  = volumes[-1]

    # Crisis: extreme volatility
    if avg_return > 0.005 or (latest_vol > avg_vol * 4 * vol_mult):
        return "crisis"

    # High volatility
    if avg_return > 0.003 or (latest_vol > avg_vol * 2.5 * vol_mult):
        return "high_volatility"

    # ADX-like calculation (simplified)
    ups   = sum(1 for r in returns if r > 0.001)
    downs = sum(1 for r in returns if r < -0.001)
    directional = abs(ups - downs) / len(returns)

    if directional > 0.5:
        return "trending_strong"
    elif directional > 0.25:
        return "trending_normal"

    return "ranging"


def _ema(data: list, period: int) -> float:
    """Hitung EMA dari list harga."""
    if not data:
        return 0.0
    k = 2 / (period + 1)
    ema = data[0]
    for price in data[1:]:
        ema = price * k + ema * (1 - k)
    return ema
