# ============================================================
# data/footprint_builder.py
# Build footprint candle dari raw aggTrade data
# Ini yang membedakan sistem kita dari Pine Script biasa
# Setiap candle menunjukkan buy vs sell di setiap price level
# ============================================================

import time
from collections import defaultdict
from loguru import logger
from data.binance_ws import get_recent_trades, current_price
from config import OF_CONFIG


class FootprintCandle:
    """
    Satu candle footprint lengkap.
    Berisi buy/sell volume di setiap price level.
    """
    def __init__(self, open_time: float, timeframe_seconds: int = 60):
        self.open_time        = open_time
        self.close_time       = open_time + timeframe_seconds
        self.timeframe        = timeframe_seconds

        self.open   = 0.0
        self.high   = 0.0
        self.low    = float("inf")
        self.close  = 0.0

        # {price_level: {"buy": qty, "sell": qty}}
        self.levels           = defaultdict(lambda: {"buy": 0.0, "sell": 0.0})
        self.total_buy_vol    = 0.0
        self.total_sell_vol   = 0.0
        self.total_volume     = 0.0
        self.delta            = 0.0   # buy - sell
        self.trades_count     = 0

        # Imbalances (ratio > 3:1)
        self.buy_imbalances   = []    # levels dominated by buyers
        self.sell_imbalances  = []    # levels dominated by sellers

        self.is_closed        = False

    def add_trade(self, price: float, qty: float, is_sell: bool):
        """Tambahkan satu transaksi ke candle ini."""
        # Tick size untuk bucketing (0.5 USD per level)
        tick    = 0.5
        level   = round(round(price / tick) * tick, 1)

        if is_sell:
            self.levels[level]["sell"] += qty
            self.total_sell_vol        += qty
        else:
            self.levels[level]["buy"]  += qty
            self.total_buy_vol         += qty

        self.total_volume += qty
        self.trades_count += 1
        self.delta         = self.total_buy_vol - self.total_sell_vol

        # Update OHLC
        if self.open == 0:
            self.open = price
        self.close = price
        self.high  = max(self.high, price)
        self.low   = min(self.low, price)

    def finalize(self):
        """Tutup candle dan hitung imbalances."""
        self.is_closed = True
        if self.low == float("inf"):
            self.low = self.open

        ratio = OF_CONFIG["imbalance_ratio"]

        for level, data in self.levels.items():
            buy  = data["buy"]
            sell = data["sell"]
            if buy > 0 and sell > 0:
                if buy / sell >= ratio:
                    self.buy_imbalances.append(level)
                elif sell / buy >= ratio:
                    self.sell_imbalances.append(level)
            elif buy > 0 and sell == 0:
                self.buy_imbalances.append(level)
            elif sell > 0 and buy == 0:
                self.sell_imbalances.append(level)

    def get_poc(self) -> float:
        """Point of Control = price level dengan volume terbanyak."""
        if not self.levels:
            return self.close
        return max(self.levels.keys(),
                   key=lambda l: self.levels[l]["buy"] + self.levels[l]["sell"])

    def get_delta_pct(self) -> float:
        """Delta sebagai persentase dari total volume."""
        if self.total_volume == 0:
            return 0.0
        return self.delta / self.total_volume * 100

    def is_absorption_bullish(self) -> bool:
        """
        Bullish absorption: sell volume besar tapi harga tidak turun.
        Candle body kecil, sell vol dominan, harga naik/flat.
        """
        if self.total_volume == 0:
            return False
        sell_ratio   = self.total_sell_vol / self.total_volume
        price_change = (self.close - self.open) / self.open if self.open > 0 else 0
        body_ratio   = abs(self.close - self.open) / (self.high - self.low) \
                       if (self.high - self.low) > 0 else 0

        return (
            sell_ratio > 0.55 and        # seller dominan
            body_ratio < 0.35 and        # body kecil
            price_change >= -0.001       # harga tidak turun
        )

    def is_absorption_bearish(self) -> bool:
        """
        Bearish absorption: buy volume besar tapi harga tidak naik.
        """
        if self.total_volume == 0:
            return False
        buy_ratio    = self.total_buy_vol / self.total_volume
        price_change = (self.close - self.open) / self.open if self.open > 0 else 0
        body_ratio   = abs(self.close - self.open) / (self.high - self.low) \
                       if (self.high - self.low) > 0 else 0

        return (
            buy_ratio > 0.55 and
            body_ratio < 0.35 and
            price_change <= 0.001
        )

    def to_dict(self) -> dict:
        """Convert ke dictionary untuk logging/storage."""
        return {
            "open_time"      : self.open_time,
            "open"           : self.open,
            "high"           : self.high,
            "low"            : self.low,
            "close"          : self.close,
            "buy_vol"        : round(self.total_buy_vol, 4),
            "sell_vol"       : round(self.total_sell_vol, 4),
            "total_vol"      : round(self.total_volume, 4),
            "delta"          : round(self.delta, 4),
            "delta_pct"      : round(self.get_delta_pct(), 2),
            "poc"            : self.get_poc(),
            "bull_absorption": self.is_absorption_bullish(),
            "bear_absorption": self.is_absorption_bearish(),
            "buy_imbalances" : len(self.buy_imbalances),
            "sell_imbalances": len(self.sell_imbalances),
            "trades"         : self.trades_count,
        }


class FootprintBuilder:
    """
    Builder yang mengumpulkan trades dan membangun footprint candles.
    Menyimpan riwayat candle terakhir untuk analisis.
    """
    def __init__(self, timeframe_seconds: int = 60, history: int = 50):
        self.timeframe        = timeframe_seconds
        self.history_size     = history
        self.current_candle   = None
        self.closed_candles   = []   # list FootprintCandle

    def process_trade(self, trade: dict):
        """Proses satu trade dan masukkan ke candle yang tepat."""
        ts    = trade["time"] / 1000  # ms to seconds
        price = trade["price"]
        qty   = trade["qty"]
        is_sell = trade["is_sell"]

        # Tentukan candle slot
        slot = int(ts // self.timeframe) * self.timeframe

        # Jika candle baru
        if self.current_candle is None:
            self.current_candle = FootprintCandle(slot, self.timeframe)

        if slot > self.current_candle.open_time:
            # Tutup candle lama
            self.current_candle.finalize()
            self.closed_candles.append(self.current_candle)
            # Jaga history size
            if len(self.closed_candles) > self.history_size:
                self.closed_candles.pop(0)
            # Buka candle baru
            self.current_candle = FootprintCandle(slot, self.timeframe)

        self.current_candle.add_trade(price, qty, is_sell)

    def build_from_recent(self, seconds: int = 300):
        """
        Build footprint dari recent trades yang sudah dikumpulkan
        WebSocket. Dipanggil setiap kali kita butuh analisis.
        """
        trades = get_recent_trades(seconds)
        if not trades:
            return []

        # Sort by time
        trades = sorted(trades, key=lambda t: t["time"])

        for trade in trades:
            self.process_trade(trade)

        return self.get_recent_candles(5)

    def get_recent_candles(self, n: int = 5) -> list:
        """Ambil N candle terakhir yang sudah closed."""
        return [c.to_dict() for c in self.closed_candles[-n:]]

    def get_current_candle(self) -> dict:
        """Ambil candle yang sedang berjalan."""
        if self.current_candle:
            return self.current_candle.to_dict()
        return {}

    def get_cumulative_delta(self, n_candles: int = 10) -> float:
        """
        Cumulative Volume Delta dari N candle terakhir.
        Fabio menggunakan ini untuk konfirmasi bias sesi.
        """
        candles = self.closed_candles[-n_candles:]
        return sum(c.delta for c in candles)

    def detect_delta_divergence(self, n: int = 5) -> str:
        """
        Deteksi divergence antara harga dan delta.
        Harga naik tapi delta turun = WARNING bearish
        Harga turun tapi delta naik = WARNING bullish
        """
        candles = self.closed_candles[-n:]
        if len(candles) < 2:
            return "NONE"

        price_change = candles[-1].close - candles[0].open
        delta_change = sum(c.delta for c in candles)

        if price_change > 0 and delta_change < 0:
            return "BEARISH_DIVERGENCE"
        elif price_change < 0 and delta_change > 0:
            return "BULLISH_DIVERGENCE"
        return "NONE"


# Singleton instance
_builder = FootprintBuilder(timeframe_seconds=60, history=50)


def get_footprint_builder() -> FootprintBuilder:
    """Ambil instance FootprintBuilder global."""
    return _builder


def get_latest_footprint(n: int = 5) -> list:
    """Helper: ambil N footprint candle terakhir."""
    _builder.build_from_recent(seconds=300)
    return _builder.get_recent_candles(n)


def get_cvd(n_candles: int = 10) -> float:
    """Helper: ambil Cumulative Volume Delta."""
    return _builder.get_cumulative_delta(n_candles)
