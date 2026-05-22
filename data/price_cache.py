# data/price_cache.py
# Cache harga dan trade data ke file
# Solusi untuk shared state antara async WebSocket dan sync scorer

import json
import os
import time

CACHE_FILE = "logs/price_cache.json"

def save_cache(price: float, buy_vol: float, sell_vol: float,
               delta: float, liq_long: float, liq_short: float):
    """Simpan data real-time ke file cache."""
    data = {
        "price"      : price,
        "buy_vol"    : buy_vol,
        "sell_vol"   : sell_vol,
        "delta"      : delta,
        "delta_pct"  : (delta / (buy_vol + sell_vol) * 100)
                       if (buy_vol + sell_vol) > 0 else 0,
        "liq_long"   : liq_long,
        "liq_short"  : liq_short,
        "timestamp"  : time.time(),
    }
    try:
        os.makedirs("logs", exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass

def load_cache() -> dict:
    """Baca data cache dari file."""
    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
        # Cache expired jika lebih dari 2 menit
        if time.time() - data.get("timestamp", 0) > 120:
            return _empty_cache()
        return data
    except Exception:
        return _empty_cache()

def _empty_cache() -> dict:
    return {
        "price"    : 0.0,
        "buy_vol"  : 0.0,
        "sell_vol" : 0.0,
        "delta"    : 0.0,
        "delta_pct": 0.0,
        "liq_long" : 0.0,
        "liq_short": 0.0,
    }
