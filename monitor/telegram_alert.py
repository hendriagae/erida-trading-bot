# ============================================================
# monitor/telegram_alert.py
# Kirim notifikasi trading ke HP via Telegram
# ============================================================

import requests
from loguru import logger
from config import TELEGRAM_CONFIG


def send_message(text: str) -> bool:
    """Kirim pesan teks ke Telegram."""
    cfg = TELEGRAM_CONFIG
    if not cfg["enabled"]:
        return False
    if not cfg["bot_token"] or not cfg["chat_id"]:
        logger.warning("Telegram belum dikonfigurasi di .env")
        return False
    try:
        url = f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage"
        resp = requests.post(url, data={
            "chat_id"    : cfg["chat_id"],
            "text"       : text,
            "parse_mode" : "HTML",
        }, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Telegram error: {e}")
        return False


def notify_entry(direction: str, price: float, sl: float,
                 tp1: float, tp2: float, tp3: float,
                 size: float, risk_usd: float,
                 tier: str, score: int) -> None:
    """Notifikasi saat bot membuka posisi."""
    if not TELEGRAM_CONFIG["notify_entry"]:
        return
    emoji = "🟢" if direction == "LONG" else "🔴"
    msg = (
        f"{emoji} <b>AOS BOT — {direction}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⚔️ Entry : <b>${price:,.2f}</b>\n"
        f"🛑 SL    : ${sl:,.2f}\n"
        f"🎯 TP1   : ${tp1:,.2f} (40%)\n"
        f"🎯 TP2   : ${tp2:,.2f} (40%)\n"
        f"🏃 TP3   : ${tp3:,.2f} (runner)\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Size  : {size:.4f} BTC\n"
        f"💸 Risk  : ${risk_usd:.2f}\n"
        f"🏅 Tier  : {tier} (score {score}/10)"
    )
    send_message(msg)


def notify_exit(direction: str, entry: float, exit_price: float,
                pnl: float, reason: str) -> None:
    """Notifikasi saat bot menutup posisi."""
    if not TELEGRAM_CONFIG["notify_exit"]:
        return
    emoji = "✅" if pnl >= 0 else "❌"
    pnl_emoji = "📈" if pnl >= 0 else "📉"
    msg = (
        f"{emoji} <b>POSISI DITUTUP</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Arah  : {direction}\n"
        f"⚔️ Entry : ${entry:,.2f}\n"
        f"🚪 Exit  : ${exit_price:,.2f}\n"
        f"{pnl_emoji} PnL   : <b>${pnl:+.2f}</b>\n"
        f"📋 Alasan: {reason}"
    )
    send_message(msg)


def notify_sl_hit(direction: str, entry: float,
                  sl: float, loss_usd: float) -> None:
    """Notifikasi stop loss kena."""
    if not TELEGRAM_CONFIG["notify_sl_hit"]:
        return
    msg = (
        f"🛑 <b>STOP LOSS HIT</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Arah  : {direction}\n"
        f"⚔️ Entry : ${entry:,.2f}\n"
        f"🛑 SL    : ${sl:,.2f}\n"
        f"📉 Loss  : <b>-${abs(loss_usd):.2f}</b>\n"
        f"💪 Next setup is coming!"
    )
    send_message(msg)


def notify_tp_hit(direction: str, tp_level: str,
                  price: float, profit_usd: float) -> None:
    """Notifikasi take profit kena."""
    if not TELEGRAM_CONFIG["notify_tp_hit"]:
        return
    msg = (
        f"🎯 <b>{tp_level} HIT!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Arah   : {direction}\n"
        f"💰 Price  : ${price:,.2f}\n"
        f"📈 Profit : <b>+${profit_usd:.2f}</b>"
    )
    send_message(msg)


def notify_signal(direction: str, score: int, tier: str,
                  reason: str, price: float) -> None:
    """Notifikasi sinyal terdeteksi (belum entry)."""
    if not TELEGRAM_CONFIG["notify_signal"]:
        return
    emoji = "🔔🟢" if direction == "LONG" else "🔔🔴"
    msg = (
        f"{emoji} <b>SINYAL {direction} TERDETEKSI</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💲 Price  : ${price:,.2f}\n"
        f"🏅 Tier   : {tier} (score {score}/10)\n"
        f"📋 Alasan : {reason}\n"
        f"⏳ Menunggu konfirmasi entry..."
    )
    send_message(msg)


def notify_daily_summary(date: str, total_trades: int,
                         wins: int, losses: int,
                         pnl: float, balance: float) -> None:
    """Ringkasan performa harian."""
    if not TELEGRAM_CONFIG["notify_daily"]:
        return
    wr = (wins / total_trades * 100) if total_trades > 0 else 0
    emoji = "📊✅" if pnl >= 0 else "📊❌"
    msg = (
        f"{emoji} <b>DAILY SUMMARY — {date}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📈 Total Trades : {total_trades}\n"
        f"✅ Wins         : {wins}\n"
        f"❌ Losses       : {losses}\n"
        f"📊 Win Rate     : {wr:.1f}%\n"
        f"💰 Daily PnL    : <b>${pnl:+.2f}</b>\n"
        f"🏦 Balance      : ${balance:,.2f}"
    )
    send_message(msg)


def notify_error(error_msg: str) -> None:
    """Notifikasi error kritis."""
    if not TELEGRAM_CONFIG["notify_error"]:
        return
    msg = (
        f"⚠️ <b>BOT ERROR</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🔴 {error_msg}\n"
        f"⏰ Cek bot segera!"
    )
    send_message(msg)


def notify_kill_switch(reason: str, balance: float) -> None:
    """Notifikasi kill switch aktif."""
    msg = (
        f"🚨 <b>KILL SWITCH AKTIF!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🛑 Alasan  : {reason}\n"
        f"🏦 Balance : ${balance:,.2f}\n"
        f"⛔ Semua trading dihentikan!\n"
        f"🔧 Cek bot dan restart manual."
    )
    send_message(msg)
