# ============================================================
# backtest/data_downloader.py
# Download historical data dari Binance untuk backtest
# ============================================================

import os
import time
import pandas as pd
from datetime import datetime, timedelta
from loguru import logger
from binance.client import Client
from config import BINANCE_API_KEY, BINANCE_SECRET_KEY, SYMBOL


def download_historical_klines(symbol: str = SYMBOL,
                                interval: str = "1m",
                                days: int = 30,
                                save_path: str = "backtest/data") -> str:
    """
    Download data OHLCV historis dari Binance.

    symbol   : trading pair
    interval : timeframe (1m, 5m, 15m, 1h, dll)
    days     : berapa hari ke belakang
    save_path: folder penyimpanan

    Return: path file CSV
    """
    os.makedirs(save_path, exist_ok=True)

    client     = Client(BINANCE_API_KEY, BINANCE_SECRET_KEY)
    end_time   = datetime.utcnow()
    start_time = end_time - timedelta(days=days)

    start_ms = int(start_time.timestamp() * 1000)
    end_ms   = int(end_time.timestamp() * 1000)

    logger.info(f"📥 Downloading {symbol} {interval} | "
                f"{days} days | {start_time.date()} → {end_time.date()}")

    all_klines = []
    current    = start_ms
    batch_size = 1000

    while current < end_ms:
        try:
            klines = client.futures_klines(
                symbol    = symbol,
                interval  = interval,
                startTime = current,
                endTime   = min(current + batch_size * _interval_ms(interval),
                               end_ms),
                limit     = batch_size,
            )
            if not klines:
                break

            all_klines.extend(klines)
            current = klines[-1][0] + 1
            logger.debug(f"Downloaded {len(all_klines)} bars...")
            time.sleep(0.1)  # Rate limit

        except Exception as e:
            logger.error(f"Download error: {e}")
            time.sleep(2)
            continue

    if not all_klines:
        logger.error("No data downloaded")
        return ""

    # Convert to DataFrame
    df = pd.DataFrame(all_klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_vol", "trades", "taker_buy_vol",
        "taker_buy_quote", "ignore"
    ])

    # Type conversion
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["open_time"]  = pd.to_datetime(df["open_time"],  unit="ms")
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")

    # Save
    filename = f"{save_path}/{symbol}_{interval}_{days}d.csv"
    df.to_csv(filename, index=False)
    logger.success(f"✅ Saved {len(df)} bars → {filename}")

    return filename


def load_klines_from_csv(filepath: str) -> list:
    """Load klines dari CSV yang sudah di-download."""
    if not os.path.exists(filepath):
        logger.error(f"File not found: {filepath}")
        return []

    df = pd.read_csv(filepath)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    klines = []
    for _, row in df.iterrows():
        klines.append({
            "open_time" : row.get("open_time", 0),
            "open"      : row["open"],
            "high"      : row["high"],
            "low"       : row["low"],
            "close"     : row["close"],
            "volume"    : row["volume"],
        })

    logger.info(f"📂 Loaded {len(klines)} bars from {filepath}")
    return klines


def _interval_ms(interval: str) -> int:
    """Convert interval string ke milliseconds."""
    mapping = {
        "1m": 60_000, "3m": 180_000, "5m": 300_000,
        "15m": 900_000, "30m": 1_800_000,
        "1h": 3_600_000, "4h": 14_400_000,
        "1d": 86_400_000,
    }
    return mapping.get(interval, 60_000)


if __name__ == "__main__":
    # Jalankan langsung untuk download data
    path = download_historical_klines(
        symbol   = "BTCUSDT",
        interval = "1m",
        days     = 30,
    )
    print(f"Downloaded: {path}")
