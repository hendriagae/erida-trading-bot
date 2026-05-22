# ============================================================
# engine/cvd_calculator.py
# Cumulative Volume Delta Calculator
# Fabio menggunakannya untuk konfirmasi bias dan deteksi whipsaw
# "CVD building down = expect whipsaw"
# ============================================================

from collections import deque
from loguru import logger
from data.binance_ws import get_recent_trades, get_buy_sell_volume


class CVDCalculator:
    """
    Hitung dan track Cumulative Volume Delta.

    CVD = akumulasi (buy_volume - sell_volume) sepanjang sesi.
    CVD naik  = buyer mendominasi sesi
    CVD turun = seller mendominasi sesi

    Divergence:
    Harga naik + CVD turun = BEARISH WARNING (fake move up)
    Harga turun + CVD naik = BULLISH WARNING (fake move down)
    """

    def __init__(self, window_size: int = 500):
        self.window      = window_size
        self.deltas      = deque(maxlen=window_size)
        self.prices      = deque(maxlen=window_size)
        self.cumulative  = 0.0
        self.session_cvd = 0.0

    def update(self, buy_vol: float, sell_vol: float, price: float):
        """Update CVD dengan data terbaru."""
        delta = buy_vol - sell_vol
        self.deltas.append(delta)
        self.prices.append(price)
        self.cumulative  += delta
        self.session_cvd += delta

    def update_from_trades(self, seconds: int = 60):
        """Update dari recent trades WebSocket."""
        trades = get_recent_trades(seconds)
        if not trades:
            return

        for trade in trades:
            delta = trade["qty"] if not trade["is_sell"] else -trade["qty"]
            self.deltas.append(delta)
            self.prices.append(trade["price"])

        flow = get_buy_sell_volume(seconds)
        self.cumulative  = sum(self.deltas)
        self.session_cvd += (flow["delta"])

    def reset_session(self):
        """Reset CVD sesi (dipanggil awal sesi baru)."""
        self.session_cvd = 0.0
        logger.debug("CVD session reset")

    def get_cvd(self) -> float:
        """Return cumulative delta saat ini."""
        return self.cumulative

    def get_session_cvd(self) -> float:
        """Return CVD sejak awal sesi."""
        return self.session_cvd

    def get_trend(self, lookback: int = 20) -> str:
        """
        Arah CVD dalam N periode terakhir.
        Fabio: CVD trend konfirmasi arah trade.
        """
        recent = list(self.deltas)[-lookback:]
        if not recent:
            return "FLAT"
        total = sum(recent)
        if total > 0.5:
            return "UP"
        elif total < -0.5:
            return "DOWN"
        return "FLAT"

    def detect_divergence(self, lookback: int = 10) -> str:
        """
        Deteksi divergence harga vs CVD.
        Fabio: "CVD building down = expect whipsaw"

        Returns:
            "BULLISH_DIV"  = harga turun tapi CVD naik
            "BEARISH_DIV"  = harga naik tapi CVD turun
            "NONE"         = tidak ada divergence
        """
        if len(self.prices) < lookback or len(self.deltas) < lookback:
            return "NONE"

        recent_prices = list(self.prices)[-lookback:]
        recent_deltas = list(self.deltas)[-lookback:]

        price_change = recent_prices[-1] - recent_prices[0]
        delta_sum    = sum(recent_deltas)

        # Threshold kecil untuk menghindari noise
        price_threshold = recent_prices[0] * 0.001  # 0.1%
        delta_threshold = 0.5

        if price_change > price_threshold and delta_sum < -delta_threshold:
            logger.warning("⚠️ BEARISH CVD DIVERGENCE — price up but delta down")
            return "BEARISH_DIV"
        elif price_change < -price_threshold and delta_sum > delta_threshold:
            logger.warning("⚠️ BULLISH CVD DIVERGENCE — price down but delta up")
            return "BULLISH_DIV"

        return "NONE"

    def get_momentum(self, fast: int = 5, slow: int = 20) -> str:
        """
        CVD momentum: apakah buying/selling pressure meningkat atau menurun?
        """
        if len(self.deltas) < slow:
            return "FLAT"

        fast_avg = sum(list(self.deltas)[-fast:]) / fast
        slow_avg = sum(list(self.deltas)[-slow:]) / slow

        if fast_avg > slow_avg * 1.2:
            return "ACCELERATING_BUY"
        elif fast_avg < slow_avg * 0.8 and fast_avg > 0:
            return "DECELERATING_BUY"
        elif fast_avg < slow_avg * 0.8 and fast_avg < 0:
            return "ACCELERATING_SELL"
        elif fast_avg > slow_avg * 1.2 and fast_avg < 0:
            return "DECELERATING_SELL"

        return "FLAT"

    def get_summary(self) -> dict:
        """Ringkasan lengkap CVD saat ini."""
        divergence = self.detect_divergence()
        return {
            "cumulative"   : round(self.cumulative, 4),
            "session_cvd"  : round(self.session_cvd, 4),
            "trend"        : self.get_trend(),
            "momentum"     : self.get_momentum(),
            "divergence"   : divergence,
            "is_bullish"   : self.cumulative > 0,
            "is_bearish"   : self.cumulative < 0,
            "has_warning"  : divergence != "NONE",
        }


# Singleton
_cvd = CVDCalculator(window_size=500)


def get_cvd_calculator() -> CVDCalculator:
    return _cvd


def get_cvd_summary() -> dict:
    """Helper: ambil summary CVD dan update dari recent trades."""
    _cvd.update_from_trades(seconds=300)
    return _cvd.get_summary()
