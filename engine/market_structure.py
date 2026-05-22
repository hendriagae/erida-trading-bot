# ============================================================
# engine/market_structure.py — LAYER 2: MARKET STRUCTURE
# ERIDA Trading Bot
#
# Implementasi Smart Money Concepts (SMC):
# → Change of Character (ChoCh) — tanda AWAL pembalikan
# → Break of Structure (BOS) — konfirmasi trend baru
# → Order Blocks — zona institusi menaruh order
# → Fair Value Gaps (FVG) — area imbalance yang akan diisi
#
# Ini yang membedakan trader profesional dari retail:
# Mereka tidak lihat candle, mereka lihat STRUKTUR.
# ============================================================

from loguru import logger


def analyze_market_structure(klines: list,
                              lookback: int = 50) -> dict:
    """
    Analisa struktur market lengkap.
    Return semua informasi struktur yang dibutuhkan.
    """
    if len(klines) < lookback:
        lookback = len(klines)

    recent  = klines[-lookback:]
    current = recent[-1]["close"]

    # Temukan swing points
    swings = _find_swing_points(recent)

    # Deteksi trend structure
    structure = _analyze_structure(swings)

    # Deteksi ChoCh dan BOS
    choch = _detect_choch(swings, recent)
    bos   = _detect_bos(swings, recent)

    # Temukan Order Blocks
    order_blocks = _find_order_blocks(recent)

    # Temukan Fair Value Gaps
    fvgs = _find_fair_value_gaps(recent)

    result = {
        "trend"           : structure["trend"],
        "higher_highs"    : structure["higher_highs"],
        "lower_lows"      : structure["lower_lows"],
        "choch"           : choch,
        "bos"             : bos,
        "order_blocks"    : order_blocks,
        "fvgs"            : fvgs,
        "current_price"   : current,
        "last_swing_high" : swings["last_high"],
        "last_swing_low"  : swings["last_low"],
    }

    # Log ringkasan
    logger.debug(
        f"Structure | Trend: {structure['trend']} | "
        f"ChoCh: {choch['detected']} | "
        f"BOS: {bos['detected']} | "
        f"OB: {len(order_blocks)} | "
        f"FVG: {len(fvgs)}"
    )

    return result


def _find_swing_points(klines: list) -> dict:
    """Temukan semua swing high dan low."""
    highs = [k["high"]  for k in klines]
    lows  = [k["low"]   for k in klines]

    swing_highs = []
    swing_lows  = []

    for i in range(2, len(klines) - 2):
        # Swing high: lebih tinggi dari 2 candle kiri dan kanan
        if (highs[i] > highs[i-1] and highs[i] > highs[i-2] and
                highs[i] > highs[i+1] and highs[i] > highs[i+2]):
            swing_highs.append({"price": highs[i], "index": i})

        # Swing low: lebih rendah dari 2 candle kiri dan kanan
        if (lows[i] < lows[i-1] and lows[i] < lows[i-2] and
                lows[i] < lows[i+1] and lows[i] < lows[i+2]):
            swing_lows.append({"price": lows[i], "index": i})

    last_high = swing_highs[-1]["price"] if swing_highs else 0
    last_low  = swing_lows[-1]["price"]  if swing_lows  else 0

    return {
        "highs"    : swing_highs,
        "lows"     : swing_lows,
        "last_high": last_high,
        "last_low" : last_low,
    }


def _analyze_structure(swings: dict) -> dict:
    """
    Tentukan trend dari urutan swing points.
    Uptrend    = Higher Highs + Higher Lows
    Downtrend  = Lower Highs + Lower Lows
    """
    highs = [s["price"] for s in swings["highs"][-4:]]
    lows  = [s["price"] for s in swings["lows"][-4:]]

    if len(highs) < 2 or len(lows) < 2:
        return {"trend": "UNDEFINED",
                "higher_highs": False, "lower_lows": False}

    higher_highs = highs[-1] > highs[-2]
    higher_lows  = lows[-1]  > lows[-2]
    lower_highs  = highs[-1] < highs[-2]
    lower_lows   = lows[-1]  < lows[-2]

    if higher_highs and higher_lows:
        trend = "UPTREND"
    elif lower_highs and lower_lows:
        trend = "DOWNTREND"
    elif higher_highs and lower_lows:
        trend = "EXPANDING"
    else:
        trend = "CONTRACTING"

    return {
        "trend"        : trend,
        "higher_highs" : higher_highs,
        "higher_lows"  : higher_lows,
        "lower_highs"  : lower_highs,
        "lower_lows"   : lower_lows,
    }


def _detect_choch(swings: dict, klines: list) -> dict:
    """
    Deteksi Change of Character (ChoCh).

    ChoCh Bullish:
    → Market sedang downtrend (lower lows)
    → Tiba-tiba candle BREAK di atas swing high sebelumnya
    → Ini adalah tanda PERTAMA kemungkinan reversal
    → Belum konfirmasi trend baru, hanya warning

    ChoCh Bearish:
    → Market sedang uptrend (higher highs)
    → Tiba-tiba candle BREAK di bawah swing low sebelumnya
    → Tanda pertama kemungkinan reversal turun
    """
    if not swings["highs"] or not swings["lows"]:
        return {"detected": False, "direction": None}

    latest       = klines[-1]
    current      = latest["close"]
    last_high    = swings["last_high"]
    last_low     = swings["last_low"]

    highs = [s["price"] for s in swings["highs"]]
    lows  = [s["price"] for s in swings["lows"]]

    # ChoCh Bullish: dalam downtrend, break di atas last high
    bull_choch = False
    if (len(highs) >= 2 and
            highs[-1] < highs[-2] and  # lower high = downtrend
            current > last_high):       # break above last high
        bull_choch = True
        logger.info(
            f"🔄 ChoCh BULLISH detected | "
            f"Price ${current:,.2f} broke above "
            f"${last_high:,.2f}"
        )

    # ChoCh Bearish: dalam uptrend, break di bawah last low
    bear_choch = False
    if (len(lows) >= 2 and
            lows[-1] > lows[-2] and    # higher low = uptrend
            current < last_low):        # break below last low
        bear_choch = True
        logger.info(
            f"🔄 ChoCh BEARISH detected | "
            f"Price ${current:,.2f} broke below "
            f"${last_low:,.2f}"
        )

    return {
        "detected"  : bull_choch or bear_choch,
        "direction" : ("BULLISH" if bull_choch else
                       "BEARISH" if bear_choch else None),
        "level"     : last_high if bull_choch else last_low,
    }


def _detect_bos(swings: dict, klines: list) -> dict:
    """
    Deteksi Break of Structure (BOS).

    BOS = konfirmasi trend BARU setelah ChoCh.
    BOS Bullish = higher high baru TERBENTUK
    BOS Bearish = lower low baru TERBENTUK

    Lebih kuat dari ChoCh karena ini konfirmasi.
    Entry terbaik: setelah BOS + retracement ke order block.
    """
    if not swings["highs"] or not swings["lows"]:
        return {"detected": False, "direction": None}

    highs = [s["price"] for s in swings["highs"]]
    lows  = [s["price"] for s in swings["lows"]]

    bull_bos = (len(highs) >= 2 and highs[-1] > highs[-2])
    bear_bos = (len(lows)  >= 2 and lows[-1]  < lows[-2])

    if bull_bos:
        logger.debug(
            f"✅ BOS BULLISH | "
            f"New high ${highs[-1]:,.2f} > ${highs[-2]:,.2f}"
        )
    if bear_bos:
        logger.debug(
            f"✅ BOS BEARISH | "
            f"New low ${lows[-1]:,.2f} < ${lows[-2]:,.2f}"
        )

    return {
        "detected"  : bull_bos or bear_bos,
        "direction" : ("BULLISH" if bull_bos else
                       "BEARISH" if bear_bos else None),
        "bull_bos"  : bull_bos,
        "bear_bos"  : bear_bos,
    }


def _find_order_blocks(klines: list) -> list:
    """
    Temukan Order Blocks.

    Order Block = candle terakhir berlawanan arah
                  SEBELUM big move dimulai.

    Logika:
    → Sebelum big move UP, ada candle bearish besar
      yang menjadi 'last bearish candle'
    → Ini adalah area di mana institusi menaruh buy orders
    → Harga SELALU kembali ke sini sebelum lanjut naik
    → Entry terbaik: beli saat harga kembali ke order block

    Bullish OB: last bearish candle sebelum move up
    Bearish OB: last bullish candle sebelum move down
    """
    order_blocks = []

    for i in range(3, len(klines) - 3):
        candle = klines[i]
        body   = candle["close"] - candle["open"]

        # Cek apakah candle ini diikuti big move
        next_3 = klines[i+1:i+4]
        if not next_3:
            continue

        next_move = next_3[-1]["close"] - next_3[0]["open"]
        avg_range = sum(
            k["high"] - k["low"] for k in klines[max(0,i-20):i]
        ) / max(1, min(20, i))

        # Big move = pergerakan > 2x average range
        is_big_move = abs(next_move) > avg_range * 2

        if is_big_move:
            # Bullish OB: candle bearish sebelum move naik
            if body < 0 and next_move > 0:
                order_blocks.append({
                    "type"   : "BULLISH",
                    "high"   : candle["high"],
                    "low"    : candle["low"],
                    "open"   : candle["open"],
                    "close"  : candle["close"],
                    "index"  : i,
                    "age"    : len(klines) - i,
                })

            # Bearish OB: candle bullish sebelum move turun
            elif body > 0 and next_move < 0:
                order_blocks.append({
                    "type"   : "BEARISH",
                    "high"   : candle["high"],
                    "low"    : candle["low"],
                    "open"   : candle["open"],
                    "close"  : candle["close"],
                    "index"  : i,
                    "age"    : len(klines) - i,
                })

    # Ambil 5 OB terbaru dan paling relevan
    recent_obs = sorted(order_blocks, key=lambda x: x["index"],
                        reverse=True)[:5]
    return recent_obs


def _find_fair_value_gaps(klines: list) -> list:
    """
    Temukan Fair Value Gaps (FVG) / Imbalances.

    FVG = area harga yang dilalui terlalu cepat
          sehingga tidak ada transaksi di sana.

    Cara identifikasi:
    → Candle 1 high < Candle 3 low = Bullish FVG
    → Candle 1 low  > Candle 3 high = Bearish FVG

    Harga SELALU kembali untuk mengisi FVG
    sebelum melanjutkan arah asli.

    Fabio menyebut ini 'Low Volume Node'
    Andrea menyebut ini 'Inefficient price delivery'
    """
    fvgs = []

    for i in range(1, len(klines) - 1):
        c1 = klines[i-1]
        c2 = klines[i]
        c3 = klines[i+1]

        # Bullish FVG: c1 high < c3 low (gap ke atas)
        if c1["high"] < c3["low"]:
            gap_size = c3["low"] - c1["high"]
            avg_range = c2["high"] - c2["low"]
            if gap_size > avg_range * 0.5:  # FVG harus cukup besar
                fvgs.append({
                    "type"     : "BULLISH",
                    "top"      : c3["low"],
                    "bottom"   : c1["high"],
                    "midpoint" : (c3["low"] + c1["high"]) / 2,
                    "size"     : gap_size,
                    "index"    : i,
                    "age"      : len(klines) - i,
                    "filled"   : False,
                })

        # Bearish FVG: c1 low > c3 high (gap ke bawah)
        elif c1["low"] > c3["high"]:
            gap_size = c1["low"] - c3["high"]
            avg_range = c2["high"] - c2["low"]
            if gap_size > avg_range * 0.5:
                fvgs.append({
                    "type"     : "BEARISH",
                    "top"      : c1["low"],
                    "bottom"   : c3["high"],
                    "midpoint" : (c1["low"] + c3["high"]) / 2,
                    "size"     : gap_size,
                    "index"    : i,
                    "age"      : len(klines) - i,
                    "filled"   : False,
                })

    # Ambil 5 FVG terbaru
    return sorted(fvgs, key=lambda x: x["index"],
                  reverse=True)[:5]


def get_nearest_order_block(price: float,
                             structure: dict,
                             direction: str) -> dict:
    """
    Ambil order block terdekat yang relevan untuk direction.
    Dipakai untuk entry dan SL placement.
    """
    obs = structure.get("order_blocks", [])
    if not obs:
        return {}

    if direction == "LONG":
        # Bullish OB di bawah harga saat ini
        candidates = [ob for ob in obs
                      if ob["type"] == "BULLISH" and
                      ob["high"] < price]
    else:
        # Bearish OB di atas harga saat ini
        candidates = [ob for ob in obs
                      if ob["type"] == "BEARISH" and
                      ob["low"] > price]

    if not candidates:
        return {}

    # Ambil yang paling dekat
    if direction == "LONG":
        return max(candidates, key=lambda x: x["high"])
    else:
        return min(candidates, key=lambda x: x["low"])


def get_nearest_fvg(price: float,
                    structure: dict,
                    direction: str) -> dict:
    """
    Ambil FVG terdekat yang relevan.
    Dipakai untuk TP target (harga akan isi FVG).
    """
    fvgs = structure.get("fvgs", [])
    if not fvgs:
        return {}

    if direction == "LONG":
        # Bullish FVG di atas harga = target
        candidates = [f for f in fvgs
                      if f["type"] == "BULLISH" and
                      f["bottom"] > price]
        if candidates:
            return min(candidates, key=lambda x: x["bottom"])
    else:
        # Bearish FVG di bawah harga = target
        candidates = [f for f in fvgs
                      if f["type"] == "BEARISH" and
                      f["top"] < price]
        if candidates:
            return max(candidates, key=lambda x: x["top"])

    return {}
