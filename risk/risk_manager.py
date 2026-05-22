# ============================================================
# risk/risk_manager.py — LAYER 7: RISK MANAGER
# ERIDA Trading Bot
#
# Penjaga keamanan akun — selalu jalan sebelum setiap trade.
# Tidak ada yang bisa bypass layer ini.
#
# Rules:
# → Max daily loss 2% → stop trading hari ini
# → Max drawdown 10% → kill switch aktif
# → Consecutive losses → kurangi size
# → Monday/Friday → kurangi size 50%
# → Sudah profit target → kurangi size (protect profit)
# ============================================================

from datetime import datetime, timezone
from loguru import logger
from config import RISK_CONFIG, SESSION_CONFIG
from data.database import get_today_trades, get_open_trades


class RiskManager:
    def __init__(self, account_balance: float):
        self.balance             = account_balance
        self.starting_balance    = account_balance
        self.session_profit      = 0.0
        self.session_loss        = 0.0
        self.consecutive_losses  = 0
        self.trades_today        = 0
        self.kill_switch_active  = False
        self._load_today_stats()

    def _load_today_stats(self):
        """Load statistik hari ini dari database."""
        try:
            trades = get_today_trades()
            for t in trades:
                pnl = t[10] if t[10] is not None else 0
                if pnl > 0:
                    self.session_profit     += pnl
                    self.consecutive_losses  = 0
                elif pnl < 0:
                    self.session_loss       += abs(pnl)
                    self.consecutive_losses += 1
                self.trades_today += 1

            logger.debug(
                f"Session stats loaded | "
                f"Profit: ${self.session_profit:.2f} | "
                f"Loss: ${self.session_loss:.2f} | "
                f"Trades: {self.trades_today}"
            )
        except Exception as e:
            logger.error(f"Load stats error: {e}")

    def update_after_trade(self, pnl: float):
        """Update stats setelah trade selesai."""
        if pnl > 0:
            self.session_profit     += pnl
            self.consecutive_losses  = 0
        else:
            self.session_loss        += abs(pnl)
            self.consecutive_losses  += 1
        self.trades_today += 1

        logger.info(
            f"📊 After trade | PnL: ${pnl:+.2f} | "
            f"Session: ${self.session_profit - self.session_loss:+.2f} | "
            f"Streak: {self.consecutive_losses} losses"
        )

    def can_trade(self) -> tuple:
        """
        Cek apakah boleh trade sekarang.
        Return: (bool, reason)
        """
        # Kill switch
        if self.kill_switch_active:
            return False, "Kill switch aktif — restart manual"

        # Daily loss limit
        daily_loss_limit = self.balance * RISK_CONFIG["max_daily_loss_pct"]
        if self.session_loss >= daily_loss_limit:
            return False, (
                f"Daily loss limit: "
                f"${self.session_loss:.2f} / ${daily_loss_limit:.2f}"
            )

        # Max drawdown
        current_dd = (self.starting_balance - self.balance) / \
                      self.starting_balance * 100
        if current_dd >= RISK_CONFIG["max_drawdown_pct"] * 100:
            self.kill_switch_active = True
            return False, f"Max drawdown: {current_dd:.1f}%"

        # Max open positions
        open_trades = get_open_trades()
        if len(open_trades) >= RISK_CONFIG["max_open_positions"]:
            return False, (
                f"Max positions: "
                f"{len(open_trades)}/{RISK_CONFIG['max_open_positions']}"
            )

        # Daily profit target tercapai → kurangi agresivitas
        daily_target = self.balance * RISK_CONFIG["max_daily_profit_pct"]
        if self.session_profit >= daily_target:
            logger.info(
                f"🎯 Daily target reached: "
                f"${self.session_profit:.2f} — reducing size"
            )
            # Tidak stop, tapi size dikurangi via get_size_multiplier

        return True, "OK"

    def get_size_multiplier(self) -> float:
        """
        Return multiplier untuk ukuran posisi.
        Kondisi buruk = size lebih kecil.
        """
        mult    = 1.0
        reasons = []
        today   = datetime.now(timezone.utc).weekday()

        # Monday (0) dan Friday (4) = worst days (Fabio)
        if today == 0:
            mult    *= 0.50
            reasons.append("Monday x0.5")
        elif today == 4:
            mult    *= 0.60
            reasons.append("Friday x0.6")
        elif today in [1, 2, 3]:
            # Tue-Thu slight bonus
            mult    *= 1.0
            reasons.append("Good day x1.0")

        # Consecutive losses
        if self.consecutive_losses >= RISK_CONFIG["max_consecutive_losses"]:
            extra    = self.consecutive_losses - RISK_CONFIG["max_consecutive_losses"]
            streak_m = max(0.2, 1.0 - (extra + 1) * 0.30)
            mult    *= streak_m
            reasons.append(f"Streak x{streak_m:.2f}")

        # Profit target hampir tercapai
        daily_target = self.balance * RISK_CONFIG["max_daily_profit_pct"]
        if self.session_profit >= daily_target * 0.8:
            mult    *= 0.5
            reasons.append("Near target x0.5")

        final = round(mult, 2)
        if final < 1.0:
            logger.debug(
                f"Size mult: {final} ({', '.join(reasons)})"
            )
        return final

    def get_session_summary(self) -> dict:
        """Ringkasan session saat ini."""
        net_pnl        = self.session_profit - self.session_loss
        daily_dd_limit = self.balance * RISK_CONFIG["max_daily_loss_pct"]

        return {
            "session_profit"     : round(self.session_profit, 2),
            "session_loss"       : round(self.session_loss, 2),
            "net_pnl"            : round(net_pnl, 2),
            "trades_today"       : self.trades_today,
            "consecutive_losses" : self.consecutive_losses,
            "loss_used_pct"      : round(
                self.session_loss / daily_dd_limit * 100
                if daily_dd_limit > 0 else 0, 1
            ),
            "kill_switch"        : self.kill_switch_active,
            "size_mult"          : self.get_size_multiplier(),
        }
