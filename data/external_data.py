# ============================================================
# data/external_data.py
# Ambil data makro eksternal: DXY, SPX, Fear&Greed, Deribit GEX
# ============================================================

import ssl
import requests
import urllib3
import yfinance as yf
from loguru import logger
from config import EXTERNAL_CONFIG

# Fix SSL certificate issue di Windows untuk Deribit
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Cache sederhana agar tidak fetch terlalu sering
_cache = {}


def get_fear_greed() -> dict:
    """
    Ambil Fear & Greed Index dari alternative.me
    0  = Extreme Fear (potensi reversal naik)
    100 = Extreme Greed (potensi reversal turun)
    """
    try:
        resp = requests.get(
            EXTERNAL_CONFIG["fear_greed_url"], timeout=10
        )
        data  = resp.json()["data"][0]
        value = int(data["value"])
        cls   = data["value_classification"]
        logger.debug(f"Fear & Greed: {value} ({cls})")
        return {"value": value, "classification": cls}
    except Exception as e:
        logger.error(f"Fear & Greed error: {e}")
        return {"value": 50, "classification": "Neutral"}


def get_macro_data() -> dict:
    """
    Ambil data makro: DXY, SPX, VIX, US10Y dari Yahoo Finance.
    Dipakai untuk bias makro harian.
    Fallback ke default jika gagal (weekend/market tutup).
    """
    result = {}
    tickers = {
        "dxy"   : EXTERNAL_CONFIG["dxy_ticker"],
        "spx"   : EXTERNAL_CONFIG["spx_ticker"],
        "vix"   : EXTERNAL_CONFIG["vix_ticker"],
        "us10y" : EXTERNAL_CONFIG["us10y_ticker"],
    }

    for name, ticker in tickers.items():
        try:
            # Pakai period lebih panjang agar dapat data meski weekend
            data = yf.download(
                ticker,
                period    = "5d",
                interval  = "1d",
                progress  = False,
                auto_adjust = True,
            )
            if data is not None and len(data) >= 2:
                # Handle multi-level columns dari yfinance terbaru
                if hasattr(data.columns, 'levels'):
                    close = data['Close'].squeeze()
                else:
                    close = data['Close']

                prev = float(close.iloc[-2])
                curr = float(close.iloc[-1])
                chg  = (curr - prev) / prev * 100 if prev > 0 else 0
                result[name] = {
                    "price"  : curr,
                    "change" : chg,
                    "trend"  : "UP" if chg > 0 else "DOWN",
                }
                logger.debug(f"Macro {name}: ${curr:.2f} ({chg:+.2f}%)")
            else:
                logger.warning(f"Macro {name}: no data (market tutup?)")
                result[name] = {"price": 0, "change": 0, "trend": "FLAT"}

        except Exception as e:
            logger.warning(f"Macro {name} skip: {e}")
            result[name] = {"price": 0, "change": 0, "trend": "FLAT"}

    return result


def get_btc_gamma_exposure() -> dict:
    """
    Ambil data options BTC dari Deribit untuk Gamma Exposure.
    Fix SSL certificate issue di Windows dengan verify=False.
    """
    try:
        base = EXTERNAL_CONFIG["deribit_url"]
        resp = requests.get(
            f"{base}/get_book_summary_by_currency",
            params  = {"currency": "BTC", "kind": "option"},
            timeout = 15,
            verify  = False,   # Fix SSL cert error di Windows
        )
        data     = resp.json().get("result", [])
        call_oi  = sum(d.get("open_interest", 0)
                       for d in data if "C" in d.get("instrument_name", ""))
        put_oi   = sum(d.get("open_interest", 0)
                       for d in data if "P" in d.get("instrument_name", ""))
        total_oi = call_oi + put_oi
        pc_ratio = put_oi / call_oi if call_oi > 0 else 1.0

        regime = (
            "CONSOLIDATING" if pc_ratio < 0.7
            else "VOLATILE"  if pc_ratio > 1.3
            else "NEUTRAL"
        )

        logger.debug(f"BTC GEX | Call OI: {call_oi:,.0f} | "
                     f"Put OI: {put_oi:,.0f} | P/C: {pc_ratio:.2f}")
        return {
            "call_oi"  : call_oi,
            "put_oi"   : put_oi,
            "total_oi" : total_oi,
            "pc_ratio" : pc_ratio,
            "regime"   : regime,
        }

    except Exception as e:
        logger.warning(f"Deribit GEX skip (tidak kritis): {e}")
        return {
            "call_oi"  : 0,
            "put_oi"   : 0,
            "total_oi" : 0,
            "pc_ratio" : 1.0,
            "regime"   : "NEUTRAL",
        }


def get_macro_bias() -> dict:
    """
    Gabungkan semua data makro menjadi bias score.
    Return: bias_score (-100 bearish to +100 bullish) dan summary.
    Sistem tetap jalan meski data makro gagal diambil.
    """
    macro = get_macro_data()
    fg    = get_fear_greed()
    gex   = get_btc_gamma_exposure()

    score = 0

    # DXY: naik = bearish BTC
    dxy_chg = macro.get("dxy", {}).get("change", 0)
    if dxy_chg < -0.3:
        score += 20
    elif dxy_chg > 0.3:
        score -= 20

    # SPX: naik = risk-on = bullish BTC
    spx_chg = macro.get("spx", {}).get("change", 0)
    if spx_chg > 0.5:
        score += 15
    elif spx_chg < -0.5:
        score -= 15

    # VIX: tinggi = fear = bearish
    vix_price = macro.get("vix", {}).get("price", 20)
    if vix_price > 30:
        score -= 20
    elif vix_price < 15:
        score += 10

    # Fear & Greed
    fg_val = fg.get("value", 50)
    if fg_val < 20:
        score += 15
    elif fg_val > 80:
        score -= 15

    # GEX
    if gex["pc_ratio"] < 0.7:
        score += 10
    elif gex["pc_ratio"] > 1.3:
        score -= 10

    score = max(-100, min(100, score))

    bias = (
        "STRONG_BULL"  if score >= 60
        else "BULL"    if score >= 20
        else "STRONG_BEAR" if score <= -60
        else "BEAR"    if score <= -20
        else "NEUTRAL"
    )

    logger.info(f"📊 Macro Bias: {bias} (score: {score})")
    return {
        "score"      : score,
        "bias"       : bias,
        "dxy"        : macro.get("dxy", {}),
        "spx"        : macro.get("spx", {}),
        "vix"        : macro.get("vix", {}),
        "fear_greed" : fg,
        "gex"        : gex,
    }
