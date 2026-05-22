# ============================================================
# data/database.py — Data Storage
# ERIDA Trading Bot
#
# Fix bug lama:
# → wins dihitung dari pnl > 0 (akurat)
# → Tambah kolom exit_price
# → Tambah tabel performance harian
# → Tambah fungsi get_open_trades yang benar
# ============================================================

import sqlite3
import os
from datetime import datetime, timezone
from loguru import logger
from config import DB_CONFIG


def get_connection():
    os.makedirs(
        os.path.dirname(DB_CONFIG["path"]),
        exist_ok=True
    )
    return sqlite3.connect(DB_CONFIG["path"])


def init_database():
    """Buat semua tabel yang dibutuhkan."""
    conn = get_connection()
    cur  = conn.cursor()

    # Tabel trades — lengkap dengan exit_price
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     TEXT DEFAULT (datetime('now')),
            symbol        TEXT DEFAULT 'BTCUSDT',
            direction     TEXT,
            entry_price   REAL,
            exit_price    REAL,
            sl_price      REAL,
            tp1_price     REAL,
            tp2_price     REAL,
            tp3_price     REAL,
            quantity      REAL,
            pnl           REAL,
            risk_usd      REAL,
            status        TEXT DEFAULT 'open',
            exit_reason   TEXT,
            tier          TEXT,
            score         INTEGER,
            regime        TEXT,
            session       TEXT,
            model_used    TEXT
        )
    """)

    # Tambah kolom exit_price jika belum ada (migration)
    try:
        cur.execute(
            "ALTER TABLE trades ADD COLUMN exit_price REAL"
        )
    except Exception:
        pass

    # Tabel signals
    cur.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     TEXT DEFAULT (datetime('now')),
            symbol        TEXT DEFAULT 'BTCUSDT',
            direction     TEXT,
            price         REAL,
            score         INTEGER,
            tier          TEXT,
            absorption    INTEGER,
            failed_auction INTEGER,
            stop_run      INTEGER,
            liq_sweep     INTEGER DEFAULT 0,
            choch         INTEGER DEFAULT 0,
            bos           INTEGER DEFAULT 0,
            bias          TEXT,
            regime        TEXT,
            session       TEXT,
            executed      INTEGER DEFAULT 0
        )
    """)

    # Tabel daily performance
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_performance (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            date          TEXT UNIQUE,
            total_trades  INTEGER DEFAULT 0,
            wins          INTEGER DEFAULT 0,
            losses        INTEGER DEFAULT 0,
            breakeven     INTEGER DEFAULT 0,
            pnl           REAL DEFAULT 0,
            win_rate      REAL DEFAULT 0,
            profit_factor REAL DEFAULT 0,
            max_drawdown  REAL DEFAULT 0,
            starting_bal  REAL DEFAULT 0,
            ending_bal    REAL DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()
    logger.info("✅ Database initialized")


def save_trade(trade: dict) -> int:
    """Simpan trade baru. Return ID."""
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO trades (
            timestamp, symbol, direction,
            entry_price, exit_price,
            sl_price, tp1_price, tp2_price, tp3_price,
            quantity, pnl, risk_usd,
            status, exit_reason,
            tier, score, regime, session, model_used
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        datetime.now(timezone.utc).isoformat(),
        trade.get("symbol", "BTCUSDT"),
        trade.get("direction"),
        trade.get("entry_price"),
        trade.get("exit_price"),
        trade.get("sl_price"),
        trade.get("tp1_price"),
        trade.get("tp2_price"),
        trade.get("tp3_price"),
        trade.get("quantity"),
        trade.get("pnl"),
        trade.get("risk_usd"),
        trade.get("status", "open"),
        trade.get("exit_reason", ""),
        trade.get("tier", ""),
        trade.get("score", 0),
        trade.get("regime", ""),
        trade.get("session", ""),
        trade.get("model_used", ""),
    ))
    trade_id = cur.lastrowid
    conn.commit()
    conn.close()
    return trade_id


def update_trade(trade_id: int, updates: dict):
    """Update trade yang sudah ada."""
    conn   = get_connection()
    cur    = conn.cursor()
    fields = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [trade_id]
    cur.execute(
        f"UPDATE trades SET {fields} WHERE id=?",
        values
    )
    conn.commit()
    conn.close()


def save_signal(signal: dict):
    """Simpan sinyal ke database."""
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO signals (
            timestamp, symbol, direction, price,
            score, tier, absorption, failed_auction,
            stop_run, liq_sweep, choch, bos,
            bias, regime, session, executed
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        datetime.now(timezone.utc).isoformat(),
        signal.get("symbol", "BTCUSDT"),
        signal.get("direction"),
        signal.get("price"),
        signal.get("score", 0),
        signal.get("tier", ""),
        int(signal.get("absorption", False)),
        int(signal.get("failed_auction", False)),
        int(signal.get("stop_run", False)),
        int(signal.get("liq_sweep", False)),
        int(signal.get("choch", False)),
        int(signal.get("bos", False)),
        signal.get("bias", ""),
        signal.get("regime", ""),
        signal.get("session", ""),
        int(signal.get("executed", False)),
    ))
    conn.commit()
    conn.close()


def get_today_trades() -> list:
    """Ambil semua trade hari ini."""
    conn  = get_connection()
    cur   = conn.cursor()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cur.execute("""
        SELECT * FROM trades
        WHERE timestamp LIKE ?
        AND status != 'open'
        ORDER BY timestamp DESC
    """, (f"{today}%",))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_open_trades() -> list:
    """Ambil semua posisi yang masih terbuka."""
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute(
        "SELECT * FROM trades WHERE status='open' "
        "ORDER BY timestamp DESC"
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_performance_stats(days: int = 30) -> dict:
    """Statistik performa N hari terakhir."""
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute("""
        SELECT pnl FROM trades
        WHERE status = 'closed'
        AND pnl IS NOT NULL
        AND timestamp >= datetime('now', ?)
        ORDER BY timestamp DESC
    """, (f"-{days} days",))
    rows = cur.fetchall()
    conn.close()

    pnls   = [r[0] for r in rows]
    if not pnls:
        return {
            "total": 0, "wins": 0, "losses": 0,
            "win_rate": 0, "profit_factor": 0,
            "total_pnl": 0,
        }

    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total  = len(pnls)
    wr     = len(wins) / total * 100 if total > 0 else 0
    gp     = sum(wins)
    gl     = abs(sum(losses))
    pf     = gp / gl if gl > 0 else 0

    return {
        "total"         : total,
        "wins"          : len(wins),
        "losses"        : len(losses),
        "win_rate"      : round(wr, 2),
        "profit_factor" : round(pf, 2),
        "total_pnl"     : round(sum(pnls), 2),
        "avg_win"       : round(gp/len(wins), 2) if wins else 0,
        "avg_loss"      : round(gl/len(losses), 2) if losses else 0,
    }


def close_all_open_trades(reason: str = "manual"):
    """Tutup semua open trades di DB."""
    conn = get_connection()
    cur  = conn.cursor()
    cur.execute(
        "UPDATE trades SET status='closed', exit_reason=? "
        "WHERE status='open'",
        (reason,)
    )
    conn.commit()
    conn.close()
