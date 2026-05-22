# ============================================================
# strategy/sl_tp_manager.py
# ERIDA Trading Bot — v2.0
# Internal SL/TP monitoring
#
# Untuk Binance Testnet yang tidak support
# conditional orders, bot pantau harga
# dan close posisi sendiri saat SL/TP tercapai.
# ============================================================

import time
from loguru import logger
from execution.order_manager import (close_position,
                                      get_position_info)
from engine.absorption import calculate_absorption_score
from engine.vwap_calculator import get_vwap_summary
from data.binance_ws import load_cache


class SLTPManager:
    """
    Manager SL/TP internal.
    Pantau harga setiap siklus dan
    close posisi saat SL/TP tercapai.
    """

    def __init__(self, direction: str,
                 entry_price: float,
                 sl: float,
                 tp1: float,
                 tp2: float,
                 tp3: float,
                 quantity: float,
                 ob_data: dict = None):
        self.direction   = direction
        self.entry       = entry_price
        self.sl          = sl
        self.tp1         = tp1
        self.tp2         = tp2
        self.tp3         = tp3
        self.quantity    = quantity
        self.ob_data     = ob_data or {}

        # State
        self.tp1_hit         = False
        self.tp2_hit         = False
        self.at_breakeven    = False
        self.trailing_active = False
        self.max_profit      = 0.0
        self._error_count    = 0

        logger.info(
            f"🛡️ SL/TP Monitor aktif | "
            f"{direction} | "
            f"SL: ${sl:,.2f} | "
            f"TP1: ${tp1:,.2f} | "
            f"TP2: ${tp2:,.2f}"
        )

    def update(self, current_price: float) -> dict:
        """
        Cek apakah SL atau TP tercapai.
        Dipanggil setiap 30 detik.
        """
        pnl = self._calc_pnl(current_price)
        self.max_profit = max(self.max_profit, pnl)

        actions = {
            "move_sl"       : False,
            "new_sl"        : self.sl,
            "close_partial" : False,
            "close_qty"     : 0.0,
            "close_all"     : False,
            "reason"        : "",
        }

        # ── CEK SL ───────────────────────────────────────────
        if self._sl_hit(current_price):
            logger.warning(
                f"🛑 SL HIT @ ${current_price:,.2f} | "
                f"SL level: ${self.sl:,.2f} | "
                f"PnL: ${pnl:+.2f}"
            )
            actions["close_all"] = True
            actions["reason"]    = f"SL hit @ ${self.sl:,.2f}"
            return actions

        # ── CEK TP1 ──────────────────────────────────────────
        if not self.tp1_hit and self._tp1_hit(current_price):
            self.tp1_hit = True
            qty = round(self.quantity * 0.40, 3)
            logger.info(
                f"🎯 TP1 HIT @ ${current_price:,.2f} | "
                f"Closing {qty:.3f} BTC"
            )
            actions["close_partial"] = True
            actions["close_qty"]     = qty
            actions["reason"]        = f"TP1 @ ${self.tp1:,.2f}"

            # Move to break even
            self._move_to_breakeven(actions, current_price)
            return actions

        # ── CEK TP2 ──────────────────────────────────────────
        if (self.tp1_hit and not self.tp2_hit and
                self._tp2_hit(current_price)):
            self.tp2_hit         = True
            self.trailing_active = True
            qty = round(self.quantity * 0.40, 3)
            logger.info(
                f"🎯 TP2 HIT @ ${current_price:,.2f} | "
                f"Closing {qty:.3f} BTC | "
                f"Trailing aktif untuk runner"
            )
            actions["close_partial"] = True
            actions["close_qty"]     = qty
            actions["reason"]        = f"TP2 @ ${self.tp2:,.2f}"
            return actions

        # ── CEK TP3 / TRAILING ───────────────────────────────
        if self.tp2_hit and self.trailing_active:
            if self._tp3_hit(current_price):
                logger.info(
                    f"🎯 TP3 HIT @ ${current_price:,.2f}!"
                )
                actions["close_all"] = True
                actions["reason"]    = f"TP3 @ ${self.tp3:,.2f}"
                return actions

            # Trail stop
            new_trail = self._calc_trail(current_price)
            if self._is_better_sl(new_trail):
                actions["move_sl"] = True
                actions["new_sl"]  = new_trail
                actions["reason"]  = f"Trail → ${new_trail:,.2f}"
                self.sl            = new_trail

        # ── BREAK EVEN sebelum TP1 ───────────────────────────
        if (not self.at_breakeven and
                not self.tp1_hit and pnl > 0):
            sl_dist = abs(self.entry - self.sl)
            if pnl >= sl_dist * self.quantity * 0.8:
                self._move_to_breakeven(actions, current_price)

        # ── REVERSAL DETECTION ───────────────────────────────
        if self.at_breakeven or self.tp1_hit:
            reversal = self._detect_reversal(current_price)
            if reversal["strong"]:
                actions["close_all"] = True
                actions["reason"]    = reversal["reason"]

        # ── VWAP CROSS EXIT ──────────────────────────────────
        if self.at_breakeven:
            vwap_data = get_vwap_summary(current_price)
            vwap      = vwap_data.get("vwap", 0)
            if vwap > 0:
                if (self.direction == "LONG" and
                        current_price < vwap * 0.999):
                    actions["close_all"] = True
                    actions["reason"]    = "Cross below VWAP"
                elif (self.direction == "SHORT" and
                        current_price > vwap * 1.001):
                    actions["close_all"] = True
                    actions["reason"]    = "Cross above VWAP"

        return actions

    def _sl_hit(self, price: float) -> bool:
        if self.direction == "LONG":
            return price <= self.sl
        return price >= self.sl

    def _tp1_hit(self, price: float) -> bool:
        if self.direction == "LONG":
            return price >= self.tp1
        return price <= self.tp1

    def _tp2_hit(self, price: float) -> bool:
        if self.direction == "LONG":
            return price >= self.tp2
        return price <= self.tp2

    def _tp3_hit(self, price: float) -> bool:
        if self.direction == "LONG":
            return price >= self.tp3
        return price <= self.tp3

    def _move_to_breakeven(self, actions: dict,
                            current_price: float):
        if self.at_breakeven:
            return
        tick   = self.entry * 0.0001
        buffer = tick * 3
        new_sl = (self.entry + buffer if self.direction == "LONG"
                  else self.entry - buffer)
        if self._is_better_sl(new_sl):
            actions["move_sl"]  = True
            actions["new_sl"]   = round(new_sl, 2)
            if not actions["reason"]:
                actions["reason"] = "Break even"
            self.sl           = new_sl
            self.at_breakeven = True
            logger.info(f"🔒 Break even @ ${new_sl:,.2f}")

    def _calc_trail(self, price: float) -> float:
        sl_dist = abs(self.entry - self.sl)
        buffer  = sl_dist * 0.5
        tick    = price * 0.0001
        if self.direction == "LONG":
            return max(price - buffer - (2 * tick), self.sl)
        return min(price + buffer + (2 * tick), self.sl)

    def _is_better_sl(self, new_sl: float) -> bool:
        if self.direction == "LONG":
            return new_sl > self.sl
        return new_sl < self.sl

    def _detect_reversal(self, current_price: float) -> dict:
        try:
            abs_data  = calculate_absorption_score(seconds=30)
            cache     = load_cache()
            delta_pct = cache.get("delta_pct", 0)

            if (self.direction == "LONG" and
                    abs_data["is_bearish"] and
                    abs_data["score"] >= 7):
                return {
                    "strong": True,
                    "reason": f"Bear absorption {abs_data['score']}/10"
                }
            if (self.direction == "SHORT" and
                    abs_data["is_bullish"] and
                    abs_data["score"] >= 7):
                return {
                    "strong": True,
                    "reason": f"Bull absorption {abs_data['score']}/10"
                }
            if self.direction == "LONG" and delta_pct < -60:
                return {
                    "strong": True,
                    "reason": f"Extreme sell delta ({delta_pct:.0f}%)"
                }
            if self.direction == "SHORT" and delta_pct > 60:
                return {
                    "strong": True,
                    "reason": f"Extreme buy delta ({delta_pct:.0f}%)"
                }
        except Exception as e:
            logger.debug(f"Reversal detection error: {e}")
        return {"strong": False, "reason": ""}

    def _calc_pnl(self, current: float) -> float:
        if self.direction == "LONG":
            return (current - self.entry) * self.quantity
        return (self.entry - current) * self.quantity

    def get_status(self, current_price: float) -> dict:
        pnl = self._calc_pnl(current_price)
        return {
            "direction"     : self.direction,
            "entry"         : self.entry,
            "current"       : current_price,
            "sl"            : self.sl,
            "tp1"           : self.tp1,
            "tp2"           : self.tp2,
            "tp3"           : self.tp3,
            "unrealized_pnl": round(pnl, 2),
            "max_profit"    : round(self.max_profit, 2),
            "tp1_hit"       : self.tp1_hit,
            "tp2_hit"       : self.tp2_hit,
            "at_breakeven"  : self.at_breakeven,
            "trailing"      : self.trailing_active,
        }
