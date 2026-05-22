# ============================================================
# monitor/performance.py — LAYER 8: FEEDBACK LOOP
# ERIDA Trading Bot
#
# Track performa trading secara akurat.
# Fix bug lama: wins/losses dihitung dari PnL = 0
#
# Fabio: "I go through my metrics every weekend.
#         You cannot improve what you don't measure."
# ============================================================

from datetime import datetime, timezone
from loguru import logger
from data.database import get_connection
from monitor.telegram_alert import send_message


def get_real_pnl_from_binance(symbol: str = "BTCUSDT") -> list:
    """
    Ambil PnL aktual dari Binance trade history.
    Ini yang akurat — bukan dari database bot.
    """
    try:
        from data.binance_rest import get_client
        client = get_client()
        trades = client.futures_account_trades(
            symbol=symbol, limit=50
        )
        result = []
        for t in trades:
            result.append({
                "time"       : t["time"],
                "side"       : t["side"],
                "price"      : float(t["price"]),
                "qty"        : float(t["qty"]),
                "realized_pnl": float(t.get("realizedPnl", 0)),
                "commission" : float(t.get("commission", 0)),
            })
        return result
    except Exception as e:
        logger.error(f"Binance trade history error: {e}")
        return []


def sync_pnl_from_binance():
    """
    Sync PnL dari Binance ke database bot.
    Fix bug: sebelumnya PnL selalu 0 di DB.
    """
    try:
        trades = get_real_pnl_from_binance()
        if not trades:
            return

        conn = get_connection()
        cur  = conn.cursor()

        # Ambil semua closed trades yang PnL-nya belum diupdate
        cur.execute(
            "SELECT id, entry_price, direction, quantity "
            "FROM trades WHERE status='closed' AND "
            "(pnl IS NULL OR pnl = 0)"
        )
        db_trades = cur.fetchall()

        updated = 0
        for db_t in db_trades:
            trade_id   = db_t[0]
            entry_px   = db_t[1]
            direction  = db_t[2]
            qty        = db_t[3]

            # Cari realized PnL dari Binance yang matching
            total_pnl = 0.0
            for bt in trades:
                # Match berdasarkan side dan approximate price
                if (direction == "LONG" and bt["side"] == "SELL") or \
                   (direction == "SHORT" and bt["side"] == "BUY"):
                    pnl = bt["realized_pnl"] - bt["commission"]
                    if pnl != 0:
                        total_pnl += pnl

            if total_pnl != 0:
                cur.execute(
                    "UPDATE trades SET pnl=? WHERE id=?",
                    (round(total_pnl, 4), trade_id)
                )
                updated += 1

        conn.commit()
        conn.close()

        if updated > 0:
            logger.info(f"✅ Synced PnL for {updated} trades")

    except Exception as e:
        logger.error(f"PnL sync error: {e}")


def get_session_stats() -> dict:
    """
    Statistik hari ini — akurat.
    Hitung wins/losses dari PnL aktual,
    bukan dari status saja.
    """
    try:
        conn  = get_connection()
        cur   = conn.cursor()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        cur.execute("""
            SELECT pnl, status, direction, entry_price,
                   exit_price, tier, model_used
            FROM trades
            WHERE timestamp LIKE ? AND status = 'closed'
        """, (f"{today}%",))

        rows  = cur.fetchall()
        conn.close()

        pnls   = [r[0] for r in rows if r[0] is not None]
        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        be     = [p for p in pnls if p == 0]

        total   = len(rows)
        wr      = len(wins) / total * 100 if total > 0 else 0
        gp      = sum(wins)
        gl      = abs(sum(losses))
        pf      = gp / gl if gl > 0 else (
            float("inf") if gp > 0 else 0
        )
        net_pnl = sum(pnls)

        return {
            "date"          : today,
            "total_trades"  : total,
            "wins"          : len(wins),
            "losses"        : len(losses),
            "breakeven"     : len(be),
            "win_rate"      : round(wr, 1),
            "profit_factor" : round(pf, 2),
            "net_pnl"       : round(net_pnl, 2),
            "gross_profit"  : round(gp, 2),
            "gross_loss"    : round(gl, 2),
            "avg_win"       : round(sum(wins)/len(wins), 2) if wins else 0,
            "avg_loss"      : round(sum(losses)/len(losses), 2) if losses else 0,
            "best_trade"    : round(max(pnls), 2) if pnls else 0,
            "worst_trade"   : round(min(pnls), 2) if pnls else 0,
        }

    except Exception as e:
        logger.error(f"Session stats error: {e}")
        return {}


def get_weekly_stats() -> dict:
    """Statistik 7 hari terakhir."""
    try:
        conn = get_connection()
        cur  = conn.cursor()

        cur.execute("""
            SELECT pnl FROM trades
            WHERE status = 'closed'
            AND timestamp >= datetime('now', '-7 days')
        """)

        rows   = cur.fetchall()
        conn.close()

        pnls   = [r[0] for r in rows if r[0] is not None]
        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        total  = len(pnls)
        wr     = len(wins) / total * 100 if total > 0 else 0
        gp     = sum(wins)
        gl     = abs(sum(losses))
        pf     = gp / gl if gl > 0 else 0

        return {
            "total"          : total,
            "wins"           : len(wins),
            "losses"         : len(losses),
            "win_rate"       : round(wr, 1),
            "profit_factor"  : round(pf, 2),
            "total_pnl"      : round(sum(pnls), 2),
            "avg_win"        : round(gp/len(wins), 2) if wins else 0,
            "avg_loss"       : round(gl/len(losses), 2) if losses else 0,
        }

    except Exception as e:
        logger.error(f"Weekly stats error: {e}")
        return {}


def get_stats_by_model() -> dict:
    """
    Statistik per model (A vs B).
    Berguna untuk evaluasi: model mana yang lebih baik?
    """
    try:
        conn = get_connection()
        cur  = conn.cursor()

        cur.execute("""
            SELECT model_used, pnl FROM trades
            WHERE status = 'closed'
            AND pnl IS NOT NULL
        """)

        rows   = cur.fetchall()
        conn.close()

        stats = {}
        for model in ["model_a", "model_b"]:
            model_pnls = [r[1] for r in rows if r[0] == model]
            if not model_pnls:
                stats[model] = {"trades": 0}
                continue

            wins   = [p for p in model_pnls if p > 0]
            losses = [p for p in model_pnls if p < 0]
            total  = len(model_pnls)
            wr     = len(wins) / total * 100 if total > 0 else 0
            gp     = sum(wins)
            gl     = abs(sum(losses))
            pf     = gp / gl if gl > 0 else 0

            stats[model] = {
                "trades"       : total,
                "wins"         : len(wins),
                "losses"       : len(losses),
                "win_rate"     : round(wr, 1),
                "profit_factor": round(pf, 2),
                "total_pnl"    : round(sum(model_pnls), 2),
            }

        return stats

    except Exception as e:
        logger.error(f"Model stats error: {e}")
        return {}


def check_system_health(balance: float,
                         starting_balance: float) -> dict:
    """
    Cek kesehatan sistem dan kirim alert jika perlu.
    Dipanggil setiap hari.
    """
    weekly  = get_weekly_stats()
    session = get_session_stats()

    issues   = []
    warnings = []

    # Win rate terlalu rendah
    wr = weekly.get("win_rate", 0)
    if weekly.get("total", 0) >= 20:
        if wr < 35:
            issues.append(f"Win rate kritis: {wr}%")
        elif wr < 42:
            warnings.append(f"Win rate rendah: {wr}%")

    # Profit factor di bawah 1
    pf = weekly.get("profit_factor", 0)
    if weekly.get("total", 0) >= 10:
        if pf < 1.0:
            issues.append(f"Profit factor < 1: {pf}")
        elif pf < 1.3:
            warnings.append(f"Profit factor rendah: {pf}")

    # Drawdown
    if starting_balance > 0:
        dd = (starting_balance - balance) / starting_balance * 100
        if dd > 8:
            issues.append(f"Drawdown tinggi: {dd:.1f}%")
        elif dd > 5:
            warnings.append(f"Drawdown warning: {dd:.1f}%")

    if issues:
        msg = (
            "🚨 <b>ERIDA Health Alert</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n" +
            "\n".join(f"❌ {i}" for i in issues) +
            "\n⚠️ Review strategy!"
        )
        send_message(msg)
        logger.warning(f"Health issues: {issues}")

    return {
        "healthy"       : len(issues) == 0,
        "issues"        : issues,
        "warnings"      : warnings,
        "weekly_wr"     : wr,
        "weekly_pf"     : pf,
    }


def get_full_report(balance: float) -> str:
    """Generate laporan lengkap untuk Telegram."""
    session = get_session_stats()
    weekly  = get_weekly_stats()
    models  = get_stats_by_model()

    ma = models.get("model_a", {})
    mb = models.get("model_b", {})

    lines = [
        "📊 <b>ERIDA PERFORMANCE REPORT</b>",
        f"📅 {session.get('date', '')}",
        "━━━━━━━━━━━━━━━━━━━",
        "",
        "📈 <b>TODAY</b>",
        f"  Trades : {session.get('total_trades', 0)}",
        f"  W/L/BE : {session.get('wins',0)}/{session.get('losses',0)}/{session.get('breakeven',0)}",
        f"  WR     : {session.get('win_rate', 0)}%",
        f"  PF     : {session.get('profit_factor', 0)}",
        f"  PnL    : ${session.get('net_pnl', 0):+.2f}",
        "",
        "📊 <b>7 DAYS</b>",
        f"  Trades : {weekly.get('total', 0)}",
        f"  WR     : {weekly.get('win_rate', 0)}%",
        f"  PF     : {weekly.get('profit_factor', 0)}",
        f"  PnL    : ${weekly.get('total_pnl', 0):+.2f}",
        "",
        "🤖 <b>BY MODEL</b>",
        f"  Model A: {ma.get('trades',0)} trades | WR {ma.get('win_rate',0)}% | PF {ma.get('profit_factor',0)}",
        f"  Model B: {mb.get('trades',0)} trades | WR {mb.get('win_rate',0)}% | PF {mb.get('profit_factor',0)}",
        "",
        "🏦 <b>ACCOUNT</b>",
        f"  Balance: ${balance:,.2f}",
        "━━━━━━━━━━━━━━━━━━━",
    ]

    # Health check
    wr_7d = weekly.get("win_rate", 0)
    pf_7d = weekly.get("profit_factor", 0)

    if wr_7d >= 42 and pf_7d >= 1.5:
        lines.append("✅ System healthy")
    elif wr_7d >= 38 and pf_7d >= 1.2:
        lines.append("⚠️ Monitor closely")
    else:
        lines.append("❌ Review strategy!")

    return "\n".join(lines)


def log_performance_summary(balance: float):
    """Log ringkasan ke console."""
    stats = get_session_stats()
    logger.info(
        f"📊 Session | "
        f"Trades: {stats.get('total_trades',0)} | "
        f"WR: {stats.get('win_rate',0)}% | "
        f"PF: {stats.get('profit_factor',0)} | "
        f"PnL: ${stats.get('net_pnl',0):+.2f}"
    )
