# ============================================================
# main.py — ERIDA Trading Bot v3.0
# Menghubungkan semua 8 layer sistem
#
# LAYER 1: World View (daily_bias)
# LAYER 2: Market Structure (liquidity, structure, session)
# LAYER 3: Orderflow Engine (absorption, cvd, ws data)
# LAYER 4: Entry Logic (5-gate confirmation)
# LAYER 5+6: SL & TP (sl_tp_manager)
# LAYER 7: Risk Manager
# LAYER 8: Feedback (performance tracking)
# ============================================================

import asyncio
import sys
import time
from datetime import datetime, timezone
from loguru import logger

# Setup logging
logger.remove()
logger.add(
    sys.stdout,
    level="INFO",
    format="<green>{time:HH:mm:ss}</green> | "
           "<level>{level: <8}</level> | {message}"
)
logger.add(
    "logs/erida.log",
    level="DEBUG",
    rotation="10 MB",
    retention="7 days",
    compression="zip",
)

from config import SYMBOL, IS_PAPER, TESTNET, BOT_MODE
from data.database import (init_database, save_trade,
                            save_signal, get_today_trades,
                            get_open_trades, update_trade,
                            get_connection)
from data.binance_rest import (get_usdt_balance, get_klines,
                                get_funding_rate,
                                get_open_interest,
                                get_ticker_price)
from execution.order_manager import (set_leverage,
                                      set_margin_mode,
                                      place_market_order,
                                      place_sl_order,
                                      place_tp_order,
                                      get_position_info,
                                      cancel_all_orders,
                                      update_sl_order)
import data.binance_ws as ws_module
from data.binance_ws import start_all_streams
from data.external_data import get_macro_bias
from engine.volume_profile import (build_volume_profile,
                                    detect_profile_shape,
                                    get_session_vp)
from engine.daily_bias import get_daily_bias
from engine.session_structure import get_session_structure
from strategy.entry_logic import get_entry_decision
from strategy.model_a import evaluate_model_a
from strategy.model_b import evaluate_model_b
from strategy.position_sizer import (calculate_position_size,
                                      calculate_sl_tp_from_structure)
from strategy.sl_tp_manager import SLTPManager
from risk.risk_manager import RiskManager
from risk.kill_switch import activate_kill_switch
from monitor.telegram_alert import (notify_entry, notify_signal,
                                     notify_daily_summary,
                                     notify_error, send_message)
from monitor.performance import log_performance_summary


# ── STATE GLOBAL ─────────────────────────────────────────────
state = {
    "balance"           : 0.0,
    "risk_manager"      : None,
    "sl_tp_manager"     : None,
    "vp"                : {},
    "macro_bias"        : {},
    "klines_1m"         : [],
    "last_vp_update"    : 0,
    "last_macro_update" : 0,
    "last_daily_bias"   : 0,
    "open_trade_id"     : None,
    "last_signal_notify" : 0,
    "last_signal_dir"   : None,
    "open_sl_order_id"  : None,
    "running"           : True,
    "prev_oi"           : 0,
    "_manage_errors"    : 0,
}

VP_UPDATE_INTERVAL      = 600    # 10 menit
POSITION_CHECK_INTERVAL = 60     # Cek posisi setiap 60 detik
MACRO_UPDATE_INTERVAL   = 3600   # 1 jam
DAILY_BIAS_INTERVAL     = 3600   # 1 jam
MAIN_LOOP_INTERVAL      = 45     # 30 detik


# ── PRICE HELPER ─────────────────────────────────────────────
def get_price() -> float:
    """WebSocket → REST fallback."""
    price = ws_module.current_price
    if price > 0:
        return price
    try:
        price = get_ticker_price(SYMBOL)
        if price > 0:
            ws_module.current_price = price
        return price
    except Exception:
        return 0.0


# ── SETUP ────────────────────────────────────────────────────
def setup():
    logger.info("=" * 55)
    logger.info("🤖 ERIDA TRADING BOT v3.0 STARTING")
    logger.info(f"   Mode    : {BOT_MODE.upper()}")
    logger.info(f"   Testnet : {TESTNET}")
    logger.info(f"   Symbol  : {SYMBOL}")
    logger.info("=" * 55)

    init_database()

    if not IS_PAPER:
        try:
            set_leverage(SYMBOL, 10)
            set_margin_mode(SYMBOL, "ISOLATED")
        except Exception as e:
            logger.warning(f"Setup leverage: {e}")

    # Balance
    if IS_PAPER:
        balance = 1000.0
        logger.info(f"[PAPER] Virtual balance: ${balance}")
    else:
        balance = get_usdt_balance()
        if balance == 0:
            logger.error("Balance = 0! Cek API key.")
            sys.exit(1)
        logger.info(f"💰 Balance: ${balance:,.2f} USDT")

    state["balance"]      = balance
    state["risk_manager"] = RiskManager(balance)

    # Sync open trades
    _sync_open_trades()

    # Initial data load
    _update_klines()
    _update_vp()
    _update_macro()

    # Daily bias awal
    try:
        bias = get_daily_bias()
        logger.info(
            f"📅 Daily Bias: {bias['bias']} "
            f"(score: {bias['score']}) | "
            f"HTF: {bias['htf_trend']}"
        )
    except Exception as e:
        logger.warning(f"Daily bias init: {e}")

    send_message(
        f"🤖 <b>ERIDA Bot v3.0 Started</b>\n"
        f"Mode: {BOT_MODE.upper()} | "
        f"Testnet: {TESTNET}\n"
        f"Balance: ${balance:,.2f}"
    )
    logger.info("✅ Setup complete — Waiting for setups...")


# ── SYNC OPEN TRADES ─────────────────────────────────────────
def _sync_open_trades():
    """
    Sync DB dengan posisi real di Binance.
    Jika DB open tapi Binance tidak → auto close di DB.
    """
    try:
        open_trades = get_open_trades()
        if not open_trades:
            state["open_trade_id"] = None
            logger.info("📋 No open trades in DB")
            return

        if IS_PAPER:
            _close_stale_db_trades("paper_sync")
            state["open_trade_id"] = None
            return

        real_pos = get_position_info(SYMBOL)
        if not real_pos:
            count = len(open_trades)
            _close_stale_db_trades("no_position_in_binance")
            state["open_trade_id"] = None
            logger.info(
                f"✅ Auto-closed {count} stale trade(s)"
            )
        else:
            state["open_trade_id"] = open_trades[-1][0]
            logger.info(
                f"📊 Resuming trade ID: "
                f"{state['open_trade_id']} | "
                f"{real_pos['direction']} | "
                f"PnL: ${real_pos['pnl']:+.2f}"
            )
            # Buat SLTPManager untuk posisi yang di-resume
            if state["sl_tp_manager"] is None:
                entry = real_pos["entry"]
                direction = real_pos["direction"]
                sl_dist = entry * 0.007
                if direction == "LONG":
                    sl  = entry - sl_dist
                    tp1 = entry + sl_dist * 1.0
                    tp2 = entry + sl_dist * 2.0
                    tp3 = entry + sl_dist * 3.5
                else:
                    sl  = entry + sl_dist
                    tp1 = entry - sl_dist * 1.0
                    tp2 = entry - sl_dist * 2.0
                    tp3 = entry - sl_dist * 3.5
                state["sl_tp_manager"] = SLTPManager(
                    direction   = direction,
                    entry_price = entry,
                    sl          = sl,
                    tp1         = tp1,
                    tp2         = tp2,
                    tp3         = tp3,
                    quantity    = real_pos["quantity"],
                )
                logger.info(
                    f"🛡️ SL/TP Monitor restored | "
                    f"{direction} | "
                    f"SL: ${sl:,.2f} | "
                    f"TP1: ${tp1:,.2f}"
                )

    except Exception as e:
        logger.error(f"Sync trades error: {e}")
        state["open_trade_id"] = None


def _close_stale_db_trades(reason: str):
    try:
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            "UPDATE trades SET status='closed', "
            "exit_reason=? WHERE status='open'",
            (reason,)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Close stale error: {e}")


# ── DATA UPDATERS ────────────────────────────────────────────
def _update_klines():
    try:
        k = get_klines(SYMBOL, interval="1m", limit=200)
        if k:
            state["klines_1m"] = k
            logger.debug(f"Klines: {len(k)} bars")
    except Exception as e:
        logger.error(f"Klines error: {e}")


def _update_vp():
    """
    Update VP menggunakan Session-based VP.
    Lebih stabil dari rolling 200 candle.
    VAH/VAL tidak bergerak setiap 30 detik.
    """
    try:
        # Prioritas: session VP (akurat)
        vp = get_session_vp()

        # Fallback: rolling VP jika session VP gagal
        if not vp and state["klines_1m"]:
            vp = build_volume_profile(state["klines_1m"], bins=50)

        if vp:
            state["vp"]             = vp
            state["last_vp_update"] = time.time()
            shape = detect_profile_shape(vp)
            logger.info(
                f"📊 VP | POC: {vp['poc']:.2f} | "
                f"VAH: {vp['vah']:.2f} | "
                f"VAL: {vp['val']:.2f} | "
                f"Shape: {shape}"
            )
    except Exception as e:
        logger.error(f"VP error: {e}")


def _update_macro():
    try:
        macro = get_macro_bias()
        state["macro_bias"]         = macro
        state["last_macro_update"]  = time.time()
        logger.info(
            f"🌍 Macro: {macro['bias']} "
            f"(score: {macro['score']})"
        )
    except Exception as e:
        logger.error(f"Macro error: {e}")
        state["macro_bias"] = {"score": 0, "bias": "NEUTRAL"}


# ── MAIN TRADING LOGIC ───────────────────────────────────────
def check_and_trade():
    """Loop utama: update data → cek sinyal → eksekusi."""
    now = time.time()

    # Update data berkala
    _update_klines()
    if now - state["last_vp_update"] > VP_UPDATE_INTERVAL:
        _update_vp()
    if now - state["last_macro_update"] > MACRO_UPDATE_INTERVAL:
        _update_macro()

    if not state["vp"] or not state["klines_1m"]:
        logger.debug("Waiting for data...")
        return

    # Harga
    price = get_price()
    if price == 0:
        logger.debug("Price = 0, waiting...")
        return

    vp      = state["vp"]
    klines  = state["klines_1m"]

    logger.info(
        f"🔄 Checking | ${price:,.2f} | "
        f"VP: {vp.get('val',0):.0f}-{vp.get('vah',0):.0f}"
    )

    # Risk check
    rm = state["risk_manager"]
    can_trade, reason = rm.can_trade()
    if not can_trade:
        logger.warning(f"⛔ {reason}")
        return

    # Manage posisi yang sudah terbuka
    if state["open_trade_id"]:
        _manage_open_position(price)
        return

    # ── ENTRY DECISION ───────────────────────────────────────
    try:
        funding   = get_funding_rate(SYMBOL)
        oi_now    = get_open_interest(SYMBOL)
        oi_prev   = state.get("prev_oi", oi_now)
        oi_change = (oi_now - oi_prev) / oi_prev if oi_prev > 0 else 0
        state["prev_oi"] = oi_now

        decision = get_entry_decision(
            price  = price,
            vp     = vp,
            klines = klines,
        )

    except Exception as e:
        logger.error(f"Entry decision error: {e}")
        return

    if not decision["should_trade"]:
        logger.debug(f"No trade: {decision.get('reason','')}")
        return

    direction = decision["direction"]
    tier      = decision["tier"]
    score     = decision["score"]
    model     = decision["model"]

    logger.info(
        f"📍 Signal | {direction} | "
        f"Model: {model.upper()} | "
        f"Tier: {tier} ({score}) | "
        f"Bias: {decision.get('daily_bias')} | "
        f"KZ: {decision.get('kill_zone')}"
    )

    # Notify sinyal — hanya 1x per 10 menit per arah
    reasons   = decision.get("reasons", [])
    now       = time.time()
    last_notif = state.get("last_signal_notify", 0)
    last_dir   = state.get("last_signal_dir", None)
    if direction != last_dir or (now - last_notif) > 600:
        notify_signal(
            direction = direction,
            score     = score,
            tier      = tier,
            reason    = ", ".join(reasons[:4]),
            price     = price,
        )
        state["last_signal_notify"] = now
        state["last_signal_dir"]    = direction

    # Eksekusi entry
    _execute_entry(decision, price)


def _execute_entry(decision: dict, price: float):
    """Eksekusi entry berdasarkan decision."""
    direction = decision["direction"]
    tier      = decision["tier"]
    model     = decision["model"]
    rm        = state["risk_manager"]

    # Evaluasi model untuk dapat SL/TP dari structure
    daily_bias = get_daily_bias()
    session    = get_session_structure()

    try:
        from engine.market_structure import analyze_market_structure
        from engine.liquidity_map import find_liquidity_pools
        from data.binance_rest import get_klines as _gk
        klines_15m = _gk(interval="15m", limit=100)
        structure  = analyze_market_structure(klines_15m, 60)
        liq_map    = find_liquidity_pools(klines_15m, 80)
    except Exception as e:
        logger.error(f"Structure fetch error: {e}")
        return

    # Pilih model
    if model == "model_a":
        sig = evaluate_model_a(
            price          = price,
            vp             = state["vp"],
            klines         = state["klines_1m"],
            daily_bias     = daily_bias,
            session        = session,
            structure      = structure,
            liq_map        = liq_map,
            direction_hint = direction,
        )
    else:
        sig = evaluate_model_b(
            price      = price,
            vp         = state["vp"],
            klines     = state["klines_1m"],
            daily_bias = daily_bias,
            session    = session,
        )

    if not sig.get("entry_valid"):
        logger.warning(
            f"Model {model} invalid: "
            f"{sig.get('skip_reason','')}"
        )
        return

    # SL/TP dari structure
    sl_tp = calculate_sl_tp_from_structure(
        direction    = direction,
        entry_price  = price,
        model_signal = sig,
        vp           = state["vp"],
    )

    # Validasi jarak minimum
    sl_dist = abs(price - sl_tp["sl"])
    tp1_dist= abs(sl_tp["tp1"] - price)
    if sl_dist < price * 0.001 or tp1_dist < price * 0.001:
        logger.warning("SL/TP terlalu dekat, skip")
        return

    # ── VALIDASI RR MINIMUM 1.5:1 (Chris Drysdale) ─────────
    if sl_dist > 0:
        rr = tp1_dist / sl_dist
        if rr < 1.5:
            logger.warning(
                f"❌ RR {rr:.2f} < 1.5 — skip | "
                f"SL dist: {sl_dist:.0f} | TP1 dist: {tp1_dist:.0f}"
            )
            return
        logger.info(f"✅ RR check passed: {rr:.2f}")

    # Hitung size
    size_mult = (decision.get("size_mult", 1.0) *
                 rm.get_size_multiplier())

    sizing = calculate_position_size(
        account_balance    = state["balance"],
        entry_price        = price,
        sl_price           = sl_tp["sl"],
        signal_tier        = tier,
        consecutive_losses = rm.consecutive_losses,
        session_profit     = rm.session_profit,
        size_mult          = size_mult,
    )

    if sizing["quantity"] == 0:
        logger.warning("Size = 0, skip")
        return

    qty = sizing["quantity"]

    logger.info(
        f"{'🟢' if direction=='LONG' else '🔴'} "
        f"EXECUTING {direction} | "
        f"${price:,.2f} | SL: ${sl_tp['sl']:,.2f} | "
        f"TP1: ${sl_tp['tp1']:,.2f} | "
        f"TP2: ${sl_tp['tp2']:,.2f} | "
        f"{qty:.3f} BTC | Risk: ${sizing['risk_usd']:.2f}"
    )

    # Place orders
    entry = place_market_order(direction, qty, SYMBOL)
    if not entry:
        logger.error("Entry order failed!")
        return

    tp1_qty = round(qty * 0.40, 3)
    tp2_qty = round(qty * 0.40, 3)
    tp3_qty = round(max(qty - tp1_qty - tp2_qty, 0.001), 3)

    sl_order  = place_sl_order(direction, qty, sl_tp["sl"])
    place_tp_order(direction, tp1_qty, sl_tp["tp1"])
    place_tp_order(direction, tp2_qty, sl_tp["tp2"])
    place_tp_order(direction, tp3_qty, sl_tp["tp3"])

    # Save SL order ID untuk trailing
    sl_order_id = sl_order.get("orderId") if sl_order else None
    state["open_sl_order_id"] = sl_order_id

    # Init SL/TP manager
    state["sl_tp_manager"] = SLTPManager(
        direction   = direction,
        entry_price = price,
        sl          = sl_tp["sl"],
        tp1         = sl_tp["tp1"],
        tp2         = sl_tp["tp2"],
        tp3         = sl_tp["tp3"],
        quantity    = qty,
        ob_data     = sig.get("ob_data", {}),
    )

    # Save to DB
    trade_id = save_trade({
        "direction"   : direction,
        "entry_price" : price,
        "sl_price"    : sl_tp["sl"],
        "tp1_price"   : sl_tp["tp1"],
        "tp2_price"   : sl_tp["tp2"],
        "tp3_price"   : sl_tp["tp3"],
        "quantity"    : qty,
        "risk_usd"    : sizing["risk_usd"],
        "status"      : "open",
        "tier"        : tier,
        "score"       : decision["score"],
        "regime"      : decision.get("structure_trend", ""),
        "session"     : decision.get("session_dir", ""),
        "model_used"  : model,
    })
    state["open_trade_id"]     = trade_id
    state["last_entry_price"]  = price
    state["last_pos_direction"] = direction
    state["last_pos_quantity"]  = sizing.get("quantity", 0.001)

    # Notify
    notify_entry(
        direction = direction,
        price     = price,
        sl        = sl_tp["sl"],
        tp1       = sl_tp["tp1"],
        tp2       = sl_tp["tp2"],
        tp3       = sl_tp["tp3"],
        size      = qty,
        risk_usd  = sizing["risk_usd"],
        tier      = tier,
        score     = decision["score"],
    )


def _manage_open_position(price: float):
    """
    Monitor dan manage posisi terbuka.
    Auto-detect jika posisi sudah ditutup di Binance.
    """
    try:
        if IS_PAPER:
            # Paper mode: simulasi saja
            state["open_trade_id"] = None
            state["sl_tp_manager"] = None
            _close_stale_db_trades("paper_auto")
            return

        # Cek posisi hanya setiap 60 detik untuk hemat API
        now = time.time()
        if (now - state.get("last_position_check", 0) 
                < POSITION_CHECK_INTERVAL):
            # Gunakan SL/TP manager saja tanpa hit API
            sltm = state.get("sl_tp_manager")
            if sltm:
                actions = sltm.update(price)
                if actions["close_all"]:
                    from execution.order_manager import close_position
                    cancel_all_orders(SYMBOL)
                    result = close_position(
                        state.get("last_pos_direction", "LONG"),
                        state.get("last_pos_quantity", 0.01)
                    )
                    logger.info(f"🚪 Exit: {actions['reason']}")
                    if result and state["open_trade_id"]:
                        direction = state.get("last_pos_direction", "LONG")
                        entry_p   = state.get("last_entry_price", price)
                        pnl_pts   = (price - entry_p) if direction == "LONG" else (entry_p - price)
                        update_trade(state["open_trade_id"], {
                            "status"     : "closed",
                            "exit_price" : price,
                            "exit_reason": actions["reason"],
                            "pnl"        : pnl_pts,
                        })
                    state["open_trade_id"]   = None
                    state["sl_tp_manager"]   = None
            return

        pos = get_position_info(SYMBOL)
        # Simpan info posisi untuk dipakai saat skip API
        if pos and not pos.get("uncertain"):
            state["last_pos_direction"] = pos.get("direction", "LONG")
            state["last_pos_quantity"]  = pos.get("quantity", 0.01)

        # FIX v2: Jika uncertain (API error) = skip, jangan close DB
        if pos and pos.get("uncertain"):
            logger.warning(
                "⚠️ Position status uncertain (API error) "
                "— keeping state, will retry next cycle"
            )
            return

        if not pos:
            # Posisi sudah tutup di Binance (verified 3x)
            logger.info(
                "✅ Position closed (SL/TP or manual) — verified"
            )

            # Update DB
            if state["open_trade_id"]:
                direction = state.get("last_pos_direction", "LONG")
                entry_p   = state.get("last_entry_price", price)
                pnl_pts   = (price - entry_p) if direction == "LONG" else (entry_p - price)
                update_trade(state["open_trade_id"], {
                    "status"      : "closed",
                    "exit_price"  : price,
                    "exit_reason" : "sl_tp_or_manual",
                    "pnl"         : pnl_pts,
                })

            _close_stale_db_trades("closed_in_binance")

            # Update risk manager
            rm = state["risk_manager"]
            rm.update_after_trade(0)

            # Reset state
            state["open_trade_id"]   = None
            state["open_sl_order_id"]= None
            state["sl_tp_manager"]   = None
            state["_manage_errors"]  = 0

            from monitor.telegram_alert import notify_exit
            direction_e = state.get("last_pos_direction", "?")
            entry_e     = state.get("last_entry_price", price)
            qty_e = state.get("last_pos_quantity", 0.001)
            pnl_e = ((price-entry_e) if direction_e=="LONG" else (entry_e-price)) * qty_e
            notify_exit(direction_e, entry_e, price, pnl_e, "sl_tp_or_manual")
            return

        # Posisi masih terbuka → update SL/TP manager
        logger.debug(
            f"Open: {pos['direction']} | "
            f"PnL: ${pos['pnl']:+.2f}"
        )
        # Update last position check time
        state["last_position_check"] = time.time()

        sltm = state.get("sl_tp_manager")
        if sltm:
            actions = sltm.update(price)

            # Eksekusi actions
            if actions["close_all"]:
                from execution.order_manager import close_position
                cancel_all_orders(SYMBOL)
                result = close_position(
                    pos["direction"], pos["quantity"]
                )
                logger.info(f"🚪 Exit: {actions['reason']}")

                # Update DB dan reset state
                if result and state["open_trade_id"]:
                    update_trade(state["open_trade_id"], {
                        "status"     : "closed",
                        "exit_price" : price,
                        "exit_reason": actions["reason"],
                    })
                _close_stale_db_trades("sl_tp_internal")
                state["open_trade_id"]    = None
                state["open_sl_order_id"] = None
                state["sl_tp_manager"]    = None

                from monitor.telegram_alert import notify_exit
                direction_e = state.get("last_pos_direction", pos["direction"])
                entry_e     = state.get("last_entry_price", price)
                qty_e = state.get("last_pos_quantity", 0.001)
                pnl_e = ((price-entry_e) if direction_e=="LONG" else (entry_e-price)) * qty_e
                notify_exit(direction_e, entry_e, price, pnl_e, actions["reason"])

            elif actions["close_partial"] and actions["close_qty"] > 0:
                from execution.order_manager import close_position
                close_position(
                    pos["direction"],
                    actions["close_qty"]
                )
                logger.info(
                    f"📤 Partial close {actions['close_qty']:.3f}: "
                    f"{actions['reason']}"
                )
                send_message(
                    f"📤 <b>Partial close</b>\n"
                    f"{actions['reason']}"
                )

            elif actions["move_sl"] and actions["new_sl"] > 0:
                # Internal SL update — update SLTPManager state
                # (tidak perlu cancel/replace order di testnet)
                logger.info(
                    f"🔒 Internal SL updated → "
                    f"${actions['new_sl']:,.2f}"
                )
                # Coba update order jika ada
                if state["open_sl_order_id"]:
                    sl_id = str(state["open_sl_order_id"])
                    if not sl_id.startswith("INTERNAL"):
                        new_sl_order = update_sl_order(
                            old_order_id = sl_id,
                            direction    = pos["direction"],
                            new_sl       = actions["new_sl"],
                            quantity     = pos["quantity"],
                        )
                        if new_sl_order:
                            state["open_sl_order_id"] = \
                                new_sl_order.get("orderId")

        state["_manage_errors"] = 0

    except Exception as e:
        logger.error(f"Manage position error: {e}")
        state["_manage_errors"] = state.get("_manage_errors", 0) + 1

        # Reset jika terlalu banyak error
        if state["_manage_errors"] >= 5:
            logger.warning("Too many errors → resetting position state")
            _close_stale_db_trades("error_reset")
            state["open_trade_id"]   = None
            state["sl_tp_manager"]   = None
            state["_manage_errors"]  = 0


# ── DAILY SUMMARY ────────────────────────────────────────────
def send_daily_summary():
    trades = get_today_trades()
    pnls   = [t[10] for t in trades if t[10] is not None]
    wins   = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    pnl    = sum(pnls)
    notify_daily_summary(
        date         = datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        total_trades = len(trades),
        wins         = wins,
        losses       = losses,
        pnl          = pnl,
        balance      = state["balance"],
    )
    log_performance_summary(state["balance"])


# ── MAIN LOOP ────────────────────────────────────────────────
async def main_loop():
    logger.info("🔄 Main loop started")
    last_summary = datetime.now(timezone.utc).date()

    # Tunggu WebSocket
    logger.info("⏳ Waiting for WebSocket...")
    for _ in range(15):
        await asyncio.sleep(1)
        if ws_module.current_price > 0:
            logger.info(
                f"✅ WebSocket ready | "
                f"${ws_module.current_price:,.2f}"
            )
            break
    else:
        logger.warning("WebSocket timeout → using REST fallback")

    while state["running"]:
        try:
            check_and_trade()

            # Daily summary jam 21:00 UTC
            now = datetime.now(timezone.utc)
            if now.hour == 21 and now.date() != last_summary:
                send_daily_summary()
                last_summary = now.date()

        except KeyboardInterrupt:
            state["running"] = False
            break
        except Exception as e:
            logger.error(f"Loop error: {e}")
            notify_error(str(e))

        await asyncio.sleep(MAIN_LOOP_INTERVAL)

    logger.info("⛔ Main loop stopped")


async def run():
    """Entry point — jalankan semua komponen."""
    setup()
    await asyncio.gather(
        start_all_streams(),
        main_loop(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("👋 ERIDA Bot stopped. Goodbye!")
