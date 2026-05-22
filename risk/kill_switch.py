# ============================================================
# risk/kill_switch.py
# Emergency stop — tutup semua posisi dan hentikan bot
# ============================================================

from loguru import logger
from execution.order_manager import cancel_all_orders, close_position, get_position_info
from monitor.telegram_alert import notify_kill_switch
from config import SYMBOL


def activate_kill_switch(reason: str, balance: float) -> bool:
    """
    Aktifkan kill switch:
    1. Cancel semua pending orders
    2. Tutup semua posisi terbuka
    3. Kirim alert ke Telegram
    """
    logger.critical(f"🚨 KILL SWITCH ACTIVATED: {reason}")

    # Step 1: Cancel semua orders
    cancel_all_orders(SYMBOL)

    # Step 2: Tutup semua posisi
    pos = get_position_info(SYMBOL)
    if pos:
        close_position(pos["direction"], pos["quantity"], SYMBOL)
        logger.info(f"Position closed: {pos['direction']} {pos['quantity']}")

    # Step 3: Alert ke HP
    notify_kill_switch(reason, balance)

    return True
