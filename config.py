# ============================================================
# config.py — Pusat pengaturan semua parameter bot
# EDIT FILE INI sesuai kebutuhan kamu
# ============================================================

import os
from dotenv import load_dotenv

load_dotenv()

# ── BINANCE API ──────────────────────────────────────────────
BINANCE_API_KEY    = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "")
TESTNET            = os.getenv("TESTNET", "true").lower() == "true"

WS_BASE = ("wss://stream.binancefuture.com/ws" if TESTNET
           else "wss://fstream.binance.com/ws")

# ── INSTRUMENT ───────────────────────────────────────────────
SYMBOL      = "BTCUSDT"
LEVERAGE    = 10
MARGIN_MODE = "ISOLATED"

# ── RISK MANAGEMENT ─────────────────────────────────────────
RISK_CONFIG = {
    "risk_per_trade_pct"     : 0.01,
    "max_risk_per_trade_pct" : 0.02,
    "max_daily_loss_pct"     : 0.02,
    "max_daily_profit_pct"   : 0.05,
    "max_drawdown_pct"       : 0.10,
    "max_open_positions"     : 2,
    "default_leverage"       : 10,
    "max_leverage"           : 20,
    "max_consecutive_losses" : 3,
    "streak_size_reduction"  : 0.50,
    "sl_atr_multiplier"      : 1.5,
    "rr_ratio"               : 2.0,
    "tp1_pct"                : 0.40,
    "tp2_pct"                : 0.40,
    "tp3_pct"                : 0.20,
}

# ── SIGNAL SCORING ───────────────────────────────────────────
SIGNAL_CONFIG = {
    "tier1_min_score" : 7,
    "tier2_min_score" : 5,
    "tier3_min_score" : 3,
    "min_trade_tier"  : "Tier2",
    "tier1_size_mult" : 1.00,
    "tier2_size_mult" : 0.65,
    "tier3_size_mult" : 0.35,
}

# ── MARKET REGIME ────────────────────────────────────────────
REGIME_CONFIG = {
    "trending_strong"  : 1.00,
    "trending_normal"  : 0.75,
    "ranging"          : 0.50,
    "high_volatility"  : 0.30,
    "crisis"           : 0.00,
    "adx_trending"     : 25,
    "vol_high_mult"    : 2.5,
}

# ── VOLUME PROFILE ───────────────────────────────────────────
VP_CONFIG = {
    "session_bars"       : 390,
    "value_area_pct"     : 0.70,
    "composite_sessions" : 10,
    "lvn_threshold"      : 0.30,
    "hvn_threshold"      : 1.50,
}

# ── ORDERFLOW ────────────────────────────────────────────────
OF_CONFIG = {
    "absorption_vol_mult"  : 2.0,
    "absorption_body_pct"  : 0.35,
    "absorption_wick_pct"  : 0.30,
    "liq_small_usd"        : 50_000,
    "liq_medium_usd"       : 500_000,
    "liq_large_usd"        : 500_000,
    "min_bubble_contracts" : 20,
    "imbalance_ratio"      : 3.0,
}

# ── FABIO MODEL ──────────────────────────────────────────────
FABIO_CONFIG = {
    "model_a_rr_min"       : 2.5,
    "model_a_breakout_vol" : 1.5,
    "model_b_rr_min"       : 1.5,
    "model_b_target"       : "POC",
    "oob_threshold_pct"    : 0.002,
}

# ── SESSION TIMING (UTC) ─────────────────────────────────────
SESSION_CONFIG = {
    "london_open"    : "07:00",
    "london_close"   : "16:00",
    "ny_open"        : "13:30",
    "ny_close"       : "20:00",
    "ny_power_hour"  : "19:00",
    "ny_prime_start" : "14:00",
    "ny_prime_end"   : "15:30",
    "best_days"      : [1, 2, 3],   # Tue-Thu
    "worst_days"     : [0, 4],       # Mon, Fri
    "worst_day_size" : 0.50,
}

# ── TELEGRAM ─────────────────────────────────────────────────
TELEGRAM_CONFIG = {
    "bot_token"      : os.getenv("TELEGRAM_BOT_TOKEN", ""),
    "chat_id"        : os.getenv("TELEGRAM_CHAT_ID", ""),
    "enabled"        : True,
    "notify_entry"   : True,
    "notify_exit"    : True,
    "notify_sl_hit"  : True,
    "notify_tp_hit"  : True,
    "notify_signal"  : True,
    "notify_daily"   : True,
    "notify_error"   : True,
}

# ── DATABASE & LOGGING ───────────────────────────────────────
DB_CONFIG = {
    "path"              : "logs/trading_bot.db",
    "trades_table"      : "trades",
    "signals_table"     : "signals",
    "market_data_table" : "market_data",
}

LOG_CONFIG = {
    "level"        : "INFO",
    "trades_log"   : "logs/trades.log",
    "signals_log"  : "logs/signals.log",
    "errors_log"   : "logs/errors.log",
    "max_size_mb"  : 10,
    "backup_count" : 5,
}

# ── EXTERNAL DATA ────────────────────────────────────────────
EXTERNAL_CONFIG = {
    "dxy_ticker"     : "DX-Y.NYB",
    "spx_ticker"     : "^GSPC",
    "vix_ticker"     : "^VIX",
    "us10y_ticker"   : "^TNX",
    "fear_greed_url" : "https://api.alternative.me/fng/?limit=1",
    "deribit_url"    : "https://www.deribit.com/api/v2/public",
    "fetch_interval" : 3600,
}

# ── BOT MODE ─────────────────────────────────────────────────
BOT_MODE = os.getenv("BOT_MODE", "paper")
IS_PAPER = BOT_MODE == "paper"

print(f"✅ Config loaded | Mode: {BOT_MODE.upper()} | "
      f"Testnet: {TESTNET} | Symbol: {SYMBOL}")
