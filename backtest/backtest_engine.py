# ============================================================
# backtest/backtest_engine.py
# Backtest engine untuk validasi strategi sebelum live
# Fabio: "I run extensive statistical tests on Python"
# "You cannot put a static model on a dynamic market"
# ============================================================

import os
from datetime import datetime
from loguru import logger
from backtest.data_downloader import load_klines_from_csv
from engine.volume_profile import build_volume_profile, get_market_state
from engine.absorption import calculate_absorption_score
from engine.failed_auction import detect_failed_auction
from strategy.position_sizer import calculate_atr, calculate_sl_tp
from config import RISK_CONFIG, SIGNAL_CONFIG


class BacktestEngine:
    """
    Simple backtest engine berdasarkan logika sistem AOS.

    Cara kerja:
    1. Load historical klines
    2. Slide window through data
    3. Di setiap bar: hitung VP, deteksi sinyal
    4. Simulasi entry/exit
    5. Hitung statistik performa
    """

    def __init__(self, initial_balance: float = 1000.0,
                 risk_pct: float = 0.01):
        self.balance         = initial_balance
        self.initial_balance = initial_balance
        self.risk_pct        = risk_pct
        self.trades          = []
        self.equity_curve    = [initial_balance]
        self.open_trade      = None

    def run(self, klines: list,
            vp_window: int = 200,
            min_score: int = 5) -> dict:
        """
        Jalankan backtest pada data klines.

        klines    : list of OHLCV dicts
        vp_window : berapa bar untuk build volume profile
        min_score : minimum signal score untuk entry
        """
        if len(klines) < vp_window + 10:
            logger.error("Not enough data for backtest")
            return {}

        logger.info(f"🔬 Starting backtest | "
                    f"{len(klines)} bars | "
                    f"Balance: ${self.balance:,.2f}")

        for i in range(vp_window, len(klines) - 1):
            window  = klines[i - vp_window:i]
            current = klines[i]
            next_k  = klines[i + 1]
            price   = current["close"]

            # Build Volume Profile dari window
            vp = build_volume_profile(window, bins=30)
            if not vp:
                continue

            # Manage open trade
            if self.open_trade:
                self._manage_trade(current, next_k)
                continue

            # Signal detection
            signal = self._detect_signal(window, current, vp)

            if signal["score"] >= min_score and signal["direction"]:
                self._open_trade(signal, current, vp)

        # Close any remaining open trade
        if self.open_trade and klines:
            self._force_close(klines[-1])

        return self._calculate_results()

    def _detect_signal(self, window: list,
                        current: dict,
                        vp: dict) -> dict:
        """Simplified signal detection untuk backtest."""
        price        = current["close"]
        market_state = get_market_state(price, vp)

        score     = 0
        direction = None

        # Out of balance = base signal (Fabio's Step 1)
        if market_state == "OUT_LOW":
            score    += 3
            direction = "LONG"
        elif market_state == "OUT_HIGH":
            score    += 3
            direction = "SHORT"

        if direction is None:
            return {"score": 0, "direction": None}

        # Failed auction check
        fa = detect_failed_auction(window[-10:], vp)
        if direction == "LONG" and fa["bull_fa"]:
            score += 2
        elif direction == "SHORT" and fa["bear_fa"]:
            score += 2

        # Volume confirmation
        avg_vol = sum(k["volume"] for k in window[-20:]) / 20
        if current["volume"] > avg_vol * 1.5:
            score += 1

        # Candle direction alignment
        if direction == "LONG" and current["close"] > current["open"]:
            score += 1
        elif direction == "SHORT" and current["close"] < current["open"]:
            score += 1

        return {"score": score, "direction": direction,
                "market_state": market_state}

    def _open_trade(self, signal: dict, current: dict, vp: dict):
        """Simulasi membuka posisi."""
        direction = signal["direction"]
        price     = current["close"]

        # ATR untuk SL (simplified)
        highs  = [current["high"]]
        lows   = [current["low"]]
        closes = [current["close"]]
        atr    = current["high"] - current["low"]  # simplified

        sl_mult = RISK_CONFIG["sl_atr_multiplier"]

        if direction == "LONG":
            sl = price - atr * sl_mult
            tp = price + atr * sl_mult * RISK_CONFIG["rr_ratio"]
        else:
            sl = price + atr * sl_mult
            tp = price - atr * sl_mult * RISK_CONFIG["rr_ratio"]

        risk_usd  = self.balance * self.risk_pct
        sl_dist   = abs(price - sl)
        if sl_dist == 0:
            return

        qty       = risk_usd / sl_dist

        self.open_trade = {
            "direction" : direction,
            "entry"     : price,
            "sl"        : sl,
            "tp"        : tp,
            "qty"       : qty,
            "risk_usd"  : risk_usd,
            "score"     : signal["score"],
            "bar_index" : len(self.trades),
        }

    def _manage_trade(self, current: dict, next_k: dict):
        """Cek apakah SL atau TP kena."""
        t    = self.open_trade
        high = current["high"]
        low  = current["low"]

        hit_sl = False
        hit_tp = False

        if t["direction"] == "LONG":
            if low <= t["sl"]:
                hit_sl = True
            elif high >= t["tp"]:
                hit_tp = True
        else:
            if high >= t["sl"]:
                hit_sl = True
            elif low <= t["tp"]:
                hit_tp = True

        if hit_sl:
            pnl = -t["risk_usd"]
            self._close_trade(t["sl"], pnl, "SL")
        elif hit_tp:
            pnl = t["risk_usd"] * RISK_CONFIG["rr_ratio"]
            self._close_trade(t["tp"], pnl, "TP")

    def _close_trade(self, exit_price: float, pnl: float, reason: str):
        """Tutup posisi dan update balance."""
        t = self.open_trade
        self.balance += pnl
        self.equity_curve.append(self.balance)

        self.trades.append({
            "direction"  : t["direction"],
            "entry"      : t["entry"],
            "exit"       : exit_price,
            "pnl"        : round(pnl, 2),
            "reason"     : reason,
            "score"      : t["score"],
        })

        self.open_trade = None

    def _force_close(self, last_candle: dict):
        """Force close sisa posisi di akhir backtest."""
        t     = self.open_trade
        price = last_candle["close"]

        if t["direction"] == "LONG":
            pnl = (price - t["entry"]) * t["qty"]
        else:
            pnl = (t["entry"] - price) * t["qty"]

        self._close_trade(price, pnl, "FORCED_CLOSE")

    def _calculate_results(self) -> dict:
        """Hitung semua statistik backtest."""
        if not self.trades:
            return {"error": "No trades"}

        wins   = [t["pnl"] for t in self.trades if t["pnl"] > 0]
        losses = [t["pnl"] for t in self.trades if t["pnl"] < 0]
        total  = len(self.trades)
        wr     = len(wins) / total * 100 if total > 0 else 0
        gp     = sum(wins)
        gl     = abs(sum(losses))
        pf     = gp / gl if gl > 0 else float("inf")

        # Max drawdown
        peak   = self.initial_balance
        max_dd = 0.0
        for val in self.equity_curve:
            if val > peak:
                peak = val
            dd = (peak - val) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)

        total_return = (self.balance - self.initial_balance) / \
                       self.initial_balance * 100

        results = {
            "total_trades"     : total,
            "wins"             : len(wins),
            "losses"           : len(losses),
            "win_rate"         : round(wr, 2),
            "profit_factor"    : round(pf, 2),
            "total_return_pct" : round(total_return, 2),
            "final_balance"    : round(self.balance, 2),
            "max_drawdown_pct" : round(max_dd, 2),
            "avg_win"          : round(sum(wins)/len(wins), 2) if wins else 0,
            "avg_loss"         : round(sum(losses)/len(losses), 2) if losses else 0,
            "best_trade"       : round(max(t["pnl"] for t in self.trades), 2),
            "worst_trade"      : round(min(t["pnl"] for t in self.trades), 2),
        }

        # Print results
        logger.info("=" * 50)
        logger.info("📊 BACKTEST RESULTS")
        logger.info("=" * 50)
        for k, v in results.items():
            logger.info(f"  {k:<25}: {v}")
        logger.info("=" * 50)

        # Fabio's benchmark: PF > 1.5 = layak trading
        if results["profit_factor"] >= 1.5 and results["win_rate"] >= 40:
            logger.success("✅ Strategy PASSES Fabio's benchmark!")
        else:
            logger.warning("⚠️ Strategy needs improvement before live trading")

        return results


if __name__ == "__main__":
    # Contoh cara menjalankan backtest
    from backtest.data_downloader import download_historical_klines, load_klines_from_csv

    # Download data dulu jika belum ada
    csv_path = "backtest/data/BTCUSDT_1m_30d.csv"
    if not os.path.exists(csv_path):
        csv_path = download_historical_klines(days=30)

    klines = load_klines_from_csv(csv_path)
    if klines:
        engine  = BacktestEngine(initial_balance=1000.0, risk_pct=0.01)
        results = engine.run(klines, vp_window=200, min_score=5)
