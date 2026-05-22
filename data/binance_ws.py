# ============================================================
# data/binance_ws.py
# WebSocket collector — data real-time dari Binance
# Versi 1.1 — Fix: cache file untuk shared state
# ============================================================

import asyncio
import json
import os
import time
from collections import deque
from loguru import logger
from config import WS_BASE, SYMBOL, OF_CONFIG

try:
    import websockets
except ImportError:
    logger.error("Install: pip install websockets")


# ── Storage in-memory (rolling window) ──────────────────────
MAX_TRADES   = 10_000
MAX_LIQIUD   = 1_000

recent_trades       = deque(maxlen=MAX_TRADES)
recent_liquidations = deque(maxlen=MAX_LIQIUD)
order_book          = {"bids": {}, "asks": {}}
current_price       = 0.0
running             = True

# Cache file path
CACHE_FILE = "logs/price_cache.json"


# ── CACHE FUNCTIONS ──────────────────────────────────────────
def _save_cache():
    """Simpan data real-time ke file cache agar bisa dibaca signal_scorer."""
    try:
        os.makedirs("logs", exist_ok=True)
        flow = get_buy_sell_volume(60)
        liq  = get_liquidation_summary(300)
        data = {
            "price"     : current_price,
            "buy_vol"   : flow["buy_vol"],
            "sell_vol"  : flow["sell_vol"],
            "delta"     : flow["delta"],
            "delta_pct" : flow["delta_pct"],
            "liq_long"  : liq["long_liquidated"],
            "liq_short" : liq["short_liquidated"],
            "timestamp" : time.time(),
        }
        with open(CACHE_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.debug(f"Cache save error: {e}")


def load_cache() -> dict:
    """Baca data cache dari file."""
    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
        if time.time() - data.get("timestamp", 0) > 120:
            return _empty_cache()
        return data
    except Exception:
        return _empty_cache()


def _empty_cache() -> dict:
    return {
        "price"     : 0.0,
        "buy_vol"   : 0.0,
        "sell_vol"  : 0.0,
        "delta"     : 0.0,
        "delta_pct" : 0.0,
        "liq_long"  : 0.0,
        "liq_short" : 0.0,
    }


# ── AGG TRADE STREAM ─────────────────────────────────────────
async def stream_agg_trades():
    """Stream setiap transaksi real-time."""
    url = f"{WS_BASE}/{SYMBOL.lower()}@aggTrade"
    logger.info(f"📡 Connecting aggTrade: {url}")
    _counter = 0

    while running:
        try:
            async with websockets.connect(url) as ws:
                logger.success("✅ aggTrade stream connected")
                async for msg in ws:
                    data  = json.loads(msg)
                    trade = {
                        "time"    : data["T"],
                        "price"   : float(data["p"]),
                        "qty"     : float(data["q"]),
                        "is_sell" : data["m"],
                        "usd_val" : float(data["p"]) * float(data["q"]),
                    }
                    recent_trades.append(trade)

                    global current_price
                    current_price = trade["price"]

                    # Simpan cache setiap 50 trades
                    _counter += 1
                    if _counter % 50 == 0:
                        _save_cache()

        except Exception as e:
            logger.warning(f"aggTrade reconnecting: {e}")
            await asyncio.sleep(3)


# ── LIQUIDATION STREAM ───────────────────────────────────────
async def stream_liquidations():
    """Stream posisi yang ter-liquidasi."""
    url = f"{WS_BASE}/{SYMBOL.lower()}@forceOrder"
    logger.info(f"📡 Connecting liquidations: {url}")

    while running:
        try:
            async with websockets.connect(url) as ws:
                logger.success("✅ Liquidation stream connected")
                async for msg in ws:
                    data = json.loads(msg)
                    o    = data.get("o", {})
                    liq  = {
                        "time"    : o.get("T", time.time() * 1000),
                        "side"    : o.get("S"),
                        "price"   : float(o.get("p", 0)),
                        "qty"     : float(o.get("q", 0)),
                        "usd_val" : float(o.get("p", 0)) * float(o.get("q", 0)),
                    }
                    recent_liquidations.append(liq)

                    threshold = OF_CONFIG["liq_medium_usd"]
                    if liq["usd_val"] >= threshold:
                        logger.warning(
                            f"💥 BIG LIQUIDATION: {liq['side']} "
                            f"${liq['usd_val']:,.0f} @ ${liq['price']:,.2f}"
                        )

        except Exception as e:
            logger.warning(f"Liquidation reconnecting: {e}")
            await asyncio.sleep(3)


# ── ORDER BOOK STREAM ────────────────────────────────────────
async def stream_order_book(depth: int = 20):
    """Stream order book depth."""
    url = f"{WS_BASE}/{SYMBOL.lower()}@depth{depth}@500ms"
    logger.info(f"📡 Connecting order book: {url}")

    while running:
        try:
            async with websockets.connect(url) as ws:
                logger.success("✅ Order book stream connected")
                async for msg in ws:
                    data = json.loads(msg)
                    for bid in data.get("b", []):
                        price, qty = float(bid[0]), float(bid[1])
                        if qty == 0:
                            order_book["bids"].pop(price, None)
                        else:
                            order_book["bids"][price] = qty
                    for ask in data.get("a", []):
                        price, qty = float(ask[0]), float(ask[1])
                        if qty == 0:
                            order_book["asks"].pop(price, None)
                        else:
                            order_book["asks"][price] = qty

        except Exception as e:
            logger.warning(f"Order book reconnecting: {e}")
            await asyncio.sleep(3)


# ── HELPER FUNCTIONS ─────────────────────────────────────────
def get_recent_trades(seconds: int = 60) -> list:
    """Ambil trades dalam N detik terakhir."""
    cutoff = (time.time() - seconds) * 1000
    return [t for t in recent_trades if t["time"] >= cutoff]


def get_recent_liquidations(seconds: int = 300) -> list:
    """Ambil liquidasi dalam N detik terakhir."""
    cutoff = (time.time() - seconds) * 1000
    return [l for l in recent_liquidations if l["time"] >= cutoff]


def get_buy_sell_volume(seconds: int = 60) -> dict:
    """Hitung volume buy vs sell dalam N detik terakhir."""
    trades    = get_recent_trades(seconds)
    buy_vol   = sum(t["qty"] for t in trades if not t["is_sell"])
    sell_vol  = sum(t["qty"] for t in trades if t["is_sell"])
    total_vol = buy_vol + sell_vol
    delta     = buy_vol - sell_vol

    return {
        "buy_vol"   : buy_vol,
        "sell_vol"  : sell_vol,
        "total_vol" : total_vol,
        "delta"     : delta,
        "delta_pct" : (delta / total_vol * 100) if total_vol > 0 else 0,
        "bias"      : "BULLISH" if delta > 0 else "BEARISH",
    }


def get_liquidation_summary(seconds: int = 300) -> dict:
    """Ringkasan liquidasi N detik terakhir."""
    liqs      = get_recent_liquidations(seconds)
    long_liq  = sum(l["usd_val"] for l in liqs if l["side"] == "SELL")
    short_liq = sum(l["usd_val"] for l in liqs if l["side"] == "BUY")

    return {
        "long_liquidated"  : long_liq,
        "short_liquidated" : short_liq,
        "total"            : long_liq + short_liq,
        "dominant"         : ("LONG_LIQ" if long_liq > short_liq
                              else "SHORT_LIQ"),
    }


def get_order_book_snapshot() -> dict:
    """Ambil snapshot order book saat ini."""
    bids = sorted(order_book["bids"].items(), reverse=True)[:10]
    asks = sorted(order_book["asks"].items())[:10]
    return {
        "bids"     : bids,
        "asks"     : asks,
        "best_bid" : bids[0][0] if bids else 0,
        "best_ask" : asks[0][0] if asks else 0,
        "spread"   : (asks[0][0] - bids[0][0]) if (bids and asks) else 0,
    }


def get_big_orders(min_usd: float = None, seconds: int = 60) -> list:
    """Ambil order besar saja."""
    if min_usd is None:
        min_usd = OF_CONFIG["min_bubble_contracts"] * current_price * 0.001
    trades = get_recent_trades(seconds)
    return [t for t in trades if t["usd_val"] >= min_usd]


# ── START ALL STREAMS ────────────────────────────────────────
async def start_all_streams():
    """Jalankan semua WebSocket stream secara bersamaan."""
    logger.info("🚀 Starting all WebSocket streams...")
    await asyncio.gather(
        stream_agg_trades(),
        stream_liquidations(),
        stream_order_book(),
    )


def stop_streams():
    """Hentikan semua stream."""
    global running
    running = False
    logger.info("⛔ WebSocket streams stopped")
