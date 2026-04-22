"""
🃏 CDVPoker Telegram Bot — v4.0
================================
Requirements:
    pip install "python-telegram-bot[job-queue]==21.9" matplotlib

Railway Variables to set:
    BOT_TOKEN        = your telegram bot token
    SUPERADMIN_IDS   = 123456789,987654321   (comma-separated telegram user IDs)
    DB_PATH          = /data/poker.db        (optional, default: poker_tournament.db)

Security:
    - Private GitHub repo = code is safe
    - BOT_TOKEN never in code, always via env variable
    - Admin commands protected by user ID check
    - /importdb admin-only
"""

import asyncio
import io
import json
import logging
import os
import requests
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta
from typing import Optional, Callable

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Document
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)

# ─────────────────────────────────────────────
#  CONFIG  (all secrets via environment)
# ─────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
DB_FILE   = os.environ.get("DB_PATH", "poker_tournament.db")

_raw_ids = os.environ.get("SUPERADMIN_IDS", "")
SUPERADMIN_IDS: set[int] = {int(x.strip()) for x in _raw_ids.split(",") if x.strip().isdigit()}

POKER_WEBSITE_URL = os.environ.get("POKER_WEBSITE_URL", "").rstrip("/")
POKER_API_TOKEN   = os.environ.get("POKER_API_TOKEN", "")

# ─────────────────────────────────────────────
#  CHANGELOG  ← update this when you deploy!
# ─────────────────────────────────────────────
CHANGELOG = [
    {
        "version": "v5.1",
        "date": "2026-04",
        "changes": [
            "🆕 poker.reflink.at — Live Shot Clock & Turnier-Info als PWA",
            "🆕 Website-Sync — Bot pusht Spielstand alle 30s automatisch",
            "🔧 Blind-Level Marker in Übersicht jetzt korrekt",
            "🔧 📋 Alle Level Button auch nach Level-Wechsel verfügbar",
        ],
    },
    {
        "version": "v5.0",
        "date": "2026-04",
        "changes": [
            "🆕 /seatdraw — Zufällige Sitzplatz-Ziehung mit Animation",
            "🆕 Streak-Tracking — Win/Loss Streaks in Spieler-Stats",
            "🆕 Bounty-Modus — /newgame 20 3h bounty:5 für Kopfgelder",
            "🆕 /history — Turnier-Archiv mit allen vergangenen Spielen",
            "🆕 Auto-Backup — täglich um Mitternacht per Telegram",
            "🔧 Heads-Up Anzeige wenn nur noch 2 Spieler übrig",
        ],
    },
    {
        "version": "v4.0",
        "date": "2025-04",
        "changes": [
            "🆕 Dynamische Spieleranzahl — kein fixer Wert mehr beim Start",
            "🆕 /importdb — Datenbank per Datei importieren",
            "🆕 /changelog — dieser Screen",
            "🆕 Private Repo Support — Token nur via Env-Variable",
            "🔧 Payout-Fix: Platz 4 bekommt exakt Buy-In zurück, Rest 50/30/20",
            "🔧 Post-game: Kurze Summary für alle, Detail-Charts für alle",
            "🔧 Leere DB beim ersten Start — Spieler werden dynamisch angelegt",
            "🔧 /newgame ohne feste Spielerzahl — berechnet sich aus /addplayer",
        ],
    },
    {
        "version": "v3.0",
        "date": "2025-03",
        "changes": [
            "🆕 Admin/Superadmin System mit /adminpanel",
            "🆕 Button-Menü für Spieler-Auswahl (/addplayer)",
            "🆕 2 vordefinierte Chip-Sets (/chipset)",
            "🆕 Alte Turniere eintragen (/addhistory)",
            "🆕 Turnier löschen (/deletetournament)",
            "🔧 Bustout-Bug gefixt — kein Auto-Bustout mehr",
            "🔧 Blind-Timer stoppt automatisch bei Turnierende",
        ],
    },
    {
        "version": "v2.0",
        "date": "2025-02",
        "changes": [
            "🆕 Dynamische Blind-Level basierend auf Turnierdauer",
            "🆕 Frühe Level 40% länger für mehr Spielspaß",
            "🆕 Profit-Charts nach Turnier (matplotlib)",
            "🆕 Kumulativer Verlauf bei /playerstats",
            "🆕 Individuelle Spieler-Zusammenfassung nach Turnier",
        ],
    },
    {
        "version": "v1.0",
        "date": "2025-01",
        "changes": [
            "🎉 Erster Release",
            "🆕 Turnier-Setup, Chip-Berechnung, Payout",
            "🆕 Shot Clock mit Blind-Struktur",
            "🆕 Spieler-Statistik & Leaderboard",
        ],
    },
]

# ─────────────────────────────────────────────
#  CHIP-SETS  ← hier anpassen!
# ─────────────────────────────────────────────
CHIPSETS = {
    "1": {
        "name": "Set A — Standard",
        "chips": {"25": 100, "100": 50, "500": 20, "1000": 10},
    },
    "2": {
        "name": "Set B — Deepstack",
        "chips": {"25": 80, "100": 60, "500": 30, "1000": 20, "5000": 5},
    },
}

# ─────────────────────────────────────────────
#  PRESET PLAYERS  ← hier anpassen!
# ─────────────────────────────────────────────
PRESET_PLAYERS = [
    "Dominik", "Alex", "Max", "Jonas", "Lukas",
    "Stefan", "Michael", "Thomas", "David", "Florian",
]

# ─────────────────────────────────────────────
#  BLIND STEPS
# ─────────────────────────────────────────────
BLIND_STEPS = [
    (25, 50), (50, 100), (75, 150), (100, 200), (150, 300),
    (200, 400), (300, 600), (400, 800), (500, 1000), (750, 1500),
    (1000, 2000), (1500, 3000), (2000, 4000), (3000, 6000), (5000, 10000),
]


def build_blind_levels(total_minutes: int) -> list[dict]:
    if total_minutes <= 90:    num_levels = 8
    elif total_minutes <= 150: num_levels = 10
    elif total_minutes <= 240: num_levels = 12
    else:                      num_levels = 14
    num_levels = min(num_levels, len(BLIND_STEPS))
    early_factor, num_early = 1.4, 3
    base = total_minutes / (num_early * early_factor + (num_levels - num_early))
    base = max(base, 5)
    levels = []
    for i in range(num_levels):
        small, big = BLIND_STEPS[i]
        mins = round(base * early_factor) if i < num_early else round(base)
        levels.append({"level": i + 1, "minutes": max(mins, 5), "small": small, "big": big})
    return levels


def get_active_blind_levels() -> list[dict]:
    saved = db_get("blind_levels")
    if saved:
        return json.loads(saved)
    return build_blind_levels(180)


# ─────────────────────────────────────────────
#  PAYOUT — Platz 4 bekommt exakt Buy-In zurück
# ─────────────────────────────────────────────
def get_payout_structure(num_players: int, buyin: float, total_pot: float) -> list[dict]:
    """
    Returns list of {place, amount} dicts.
    For 9+ players: place 4 gets exactly buyin back, rest split 50/30/20.
    For 6-8 players: top 3 split 50/30/20.
    For 5 or less: top 2 split 65/35.
    """
    if num_players <= 5:
        return [
            {"place": 1, "amount": round(total_pot * 0.65, 2)},
            {"place": 2, "amount": round(total_pot * 0.35, 2)},
        ]
    elif num_players <= 8:
        return [
            {"place": 1, "amount": round(total_pot * 0.50, 2)},
            {"place": 2, "amount": round(total_pot * 0.30, 2)},
            {"place": 3, "amount": round(total_pot * 0.20, 2)},
        ]
    elif num_players <= 11:
        # Platz 4 bekommt exakt Buy-In zurück
        place4_amount = buyin
        remaining = total_pot - place4_amount
        return [
            {"place": 1, "amount": round(remaining * 0.50, 2)},
            {"place": 2, "amount": round(remaining * 0.30, 2)},
            {"place": 3, "amount": round(remaining * 0.20, 2)},
            {"place": 4, "amount": place4_amount},
        ]
    else:
        # 12+ players: top 5, Platz 4+5 bekommen Buy-In zurück
        place45 = buyin
        remaining = total_pot - (place45 * 2)
        return [
            {"place": 1, "amount": round(remaining * 0.50, 2)},
            {"place": 2, "amount": round(remaining * 0.30, 2)},
            {"place": 3, "amount": round(remaining * 0.20, 2)},
            {"place": 4, "amount": place45},
            {"place": 5, "amount": place45},
        ]


# ─────────────────────────────────────────────
#  DATABASE
# ─────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS players (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        added_at TEXT DEFAULT (datetime('now')),
        total_tournaments INTEGER DEFAULT 0,
        total_wins INTEGER DEFAULT 0,
        total_earnings REAL DEFAULT 0,
        total_buyins REAL DEFAULT 0,
        current_streak INTEGER DEFAULT 0,
        best_streak INTEGER DEFAULT 0,
        worst_streak INTEGER DEFAULT 0
    )""")
    # Add streak columns to existing DBs (migration)
    for col, default in [("current_streak","0"),("best_streak","0"),("worst_streak","0")]:
        try:
            c.execute(f"ALTER TABLE players ADD COLUMN {col} INTEGER DEFAULT {default}")
        except Exception:
            pass
    c.execute("""CREATE TABLE IF NOT EXISTS tournaments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT DEFAULT (datetime('now')),
        num_players INTEGER,
        buyin_amount REAL,
        total_pot REAL,
        chip_config TEXT DEFAULT '{}',
        status TEXT DEFAULT 'setup',
        notes TEXT DEFAULT '',
        bounty_amount REAL DEFAULT 0
    )""")
    try:
        c.execute("ALTER TABLE tournaments ADD COLUMN bounty_amount REAL DEFAULT 0")
    except Exception:
        pass
    c.execute("""CREATE TABLE IF NOT EXISTS results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER,
        player_name TEXT,
        place INTEGER,
        payout REAL,
        bounties_won INTEGER DEFAULT 0,
        bounty_payout REAL DEFAULT 0,
        FOREIGN KEY (tournament_id) REFERENCES tournaments(id)
    )""")
    for col, typ, default in [("bounties_won","INTEGER","0"),("bounty_payout","REAL","0")]:
        try:
            c.execute(f"ALTER TABLE results ADD COLUMN {col} {typ} DEFAULT {default}")
        except Exception:
            pass
    c.execute("""CREATE TABLE IF NOT EXISTS admins (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        role TEXT DEFAULT 'admin',
        added_at TEXT DEFAULT (datetime('now'))
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS bot_state (
        key TEXT PRIMARY KEY,
        value TEXT
    )""")
    conn.commit()
    conn.close()


def db_get(key: str) -> Optional[str]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT value FROM bot_state WHERE key=?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def db_set(key: str, value: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO bot_state (key,value) VALUES (?,?)", (key, value))
    conn.commit()
    conn.close()


def db_del(key: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM bot_state WHERE key=?", (key,))
    conn.commit()
    conn.close()


def _do_sync_request(payload: dict):
    if not POKER_WEBSITE_URL or not POKER_API_TOKEN:
        return
    try:
        requests.post(f"{POKER_WEBSITE_URL}/api/update.php", json=payload, timeout=3)
    except Exception:
        pass


async def sync_to_website(status: str = "running"):
    if not POKER_WEBSITE_URL or not POKER_API_TOKEN:
        return
    try:
        players     = json.loads(db_get("active_players") or "[]")
        busted      = json.loads(db_get("busted_players") or "[]")
        current_lvl = int(db_get("current_blind_level") or "1")
        levels      = get_active_blind_levels()
        level = levels[current_lvl - 1] if 1 <= current_lvl <= len(levels) else None
        buyin     = float(db_get("buyin_amount") or "0")
        num       = len(players)
        total_pot = buyin * num
        payouts   = []
        if num > 0 and buyin > 0:
            try:
                payouts = get_payout_structure(num, buyin, total_pot)
            except Exception:
                pass
        payload = {
            "token":                  POKER_API_TOKEN,
            "status":                 status,
            "blind_level":            current_lvl,
            "small_blind":            level["small"]   if level else 0,
            "big_blind":              level["big"]     if level else 0,
            "level_duration_minutes": level["minutes"] if level else 0,
            "level_start_time":       (db_get("blind_start_time") or "") + "Z",
            "active_players":         players,
            "busted_players":         busted,
            "total_players":          num,
            "buyin":                  buyin,
            "total_pot":              total_pot,
            "bounty":                 float(db_get("bounty_amount") or "0"),
            "payout_structure":       payouts,
        }
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _do_sync_request, payload)
    except Exception:
        pass


def clear_session():
    for key in ["active_players", "buyin_amount", "chip_config",
                "current_blind_level", "blind_start_time", "blind_running",
                "busted_players", "blind_levels", "tournament_duration",
                "bounty_amount", "bounty_kills"]:
        db_del(key)


# ─────────────────────────────────────────────
#  PERMISSIONS
# ─────────────────────────────────────────────
def is_superadmin(user_id: int) -> bool:
    return user_id in SUPERADMIN_IDS


def is_admin(user_id: int) -> bool:
    if is_superadmin(user_id):
        return True
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT role FROM admins WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row is not None


async def require_admin(update: Update) -> bool:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Nur Admins können diesen Befehl nutzen.")
        return False
    return True


async def require_superadmin(update: Update) -> bool:
    if not is_superadmin(update.effective_user.id):
        await update.message.reply_text("⛔ Nur Superadmins.")
        return False
    return True


# ─────────────────────────────────────────────
#  CHIP CALCULATOR
# ─────────────────────────────────────────────
def calculate_chip_distribution(chip_config: dict, num_players: int) -> dict:
    chips_per_player = {}
    value_per_player = 0
    for denom, count in chip_config.items():
        per = count // num_players
        chips_per_player[denom] = per
        value_per_player += int(denom) * per
    total = sum(int(d) * c for d, c in chip_config.items())
    return {"chips_per_player": chips_per_player, "value_per_player": value_per_player, "total_value": total}


# ─────────────────────────────────────────────
#  /start  /help
# ─────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    mark = " 👑" if is_superadmin(uid) else (" 🔑" if is_admin(uid) else "")
    text = (
        f"🃏 *CDVPoker Bot v5.0{mark}*\n\n"
        "🎮 *Turnier*\n"
        "`/newgame 20 3h` — Turnier starten\n"
        "`/newgame 20 3h bounty:5` — Mit Bounty-Modus 💀\n"
        "`/addplayer` — Spieler wählen (Buttons)\n"
        "`/seatdraw` — Zufällige Sitzplätze 🎲\n"
        "`/chipset` — Chip-Set wählen\n"
        "`/calculate` — Chip-Verteilung\n"
        "`/payout` — Payouts anzeigen\n"
        "`/bustout` — Spieler ausscheiden\n"
        "`/endtournament` — Turnier beenden\n\n"
        "⏱ *Shot Clock*\n"
        "`/shotclock` — Timer starten\n"
        "`/blinds` — Aktuelle Blinds & Zeit\n"
        "`/nextlevel` `/stopblind` `/blindstructure`\n\n"
        "📊 *Statistik*\n"
        "`/stats` — Leaderboard\n"
        "`/playerstats [Name]` — Stats + Streaks + Grafik\n"
        "`/history` — Turnier-Archiv\n"
        "`/status` — Turnierstatus\n\n"
        "🔑 *Admin*\n"
        "`/adminpanel` — Admin-Funktionen\n\n"
        "📋 *Info*\n"
        "`/changelog` — Was ist neu?\n"
        "`/help` — Diese Hilfe\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


# ─────────────────────────────────────────────
#  /changelog
# ─────────────────────────────────────────────
async def changelog_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["📋 *CDVPoker — Changelog*\n"]
    for entry in CHANGELOG:
        lines.append(f"*{entry['version']}* — _{entry['date']}_")
        for change in entry["changes"]:
            lines.append(f"  {change}")
        lines.append("")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ─────────────────────────────────────────────
#  NEW GAME — no fixed player count, dynamic!
# ─────────────────────────────────────────────
async def new_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return
    clear_session()

    # Usage: /newgame BUYIN DURATION [bounty:AMOUNT]
    # e.g.   /newgame 20 3h
    #        /newgame 20 3h bounty:5
    if len(context.args) >= 2:
        try:
            buyin    = float(context.args[0])
            dur_str  = context.args[1].lower().replace("h", "").replace("std", "")
            total_minutes = int(float(dur_str) * 60)

            # Check for bounty parameter
            bounty = 0.0
            for arg in context.args[2:]:
                if arg.lower().startswith("bounty:"):
                    bounty = float(arg.split(":")[1])

            db_set("buyin_amount", str(buyin))
            db_set("tournament_duration", str(total_minutes))
            if bounty > 0:
                db_set("bounty_amount", str(bounty))
                db_set("bounty_kills", "{}")  # {killer: count}

            levels = build_blind_levels(total_minutes)
            db_set("blind_levels", json.dumps(levels))

            blind_preview = [
                f"  Lvl {l['level']}: {l['small']:,}/{l['big']:,} — {l['minutes']}min"
                + (" 🔒" if l['level'] <= 3 else "")
                for l in levels[:4]
            ]
            blind_preview.append(f"  ... ({len(levels)} Level, {sum(l['minutes'] for l in levels)} min)")

            bounty_info = f"\n💀 *Bounty-Modus: {bounty:.0f}€ pro Eliminierung!*" if bounty > 0 else ""

            await update.message.reply_text(
                f"🎰 *Turnier eingerichtet!*\n\n"
                f"💶 Buy-In: *{buyin:.0f}€*"
                + (f" + {bounty:.0f}€ Bounty" if bounty > 0 else "") +
                f"\n⏱ Geplante Dauer: *{total_minutes//60}h{total_minutes%60:02d}m*"
                + bounty_info + "\n\n"
                f"🎯 *Blind-Vorschau:*\n" + "\n".join(blind_preview) + "\n\n"
                f"📋 *Nächste Schritte:*\n"
                f"1️⃣ /addplayer — Spieler wählen\n"
                f"2️⃣ /chipset — Chip-Set wählen\n"
                f"3️⃣ /shotclock — Timer starten\n"
                f"4️⃣ /bustout — Bustouts eintragen",
                parse_mode="Markdown"
            )
            return
        except (ValueError, IndexError):
            pass

    await update.message.reply_text(
        "🎰 *Neues Turnier*\n\n"
        "`/newgame [Buy-In €] [Dauer]`\n"
        "`/newgame [Buy-In €] [Dauer] bounty:[€]`\n\n"
        "Beispiele:\n"
        "`/newgame 20 3h`\n"
        "`/newgame 20 3h bounty:5`\n"
        "`/newgame 25 4h bounty:10`\n\n"
        "_Spieleranzahl wird automatisch aus /addplayer berechnet!_",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────
#  PLAYER MENU
# ─────────────────────────────────────────────
def _build_player_keyboard(active: list) -> list:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT name FROM players ORDER BY total_tournaments DESC, name ASC")
    db_players = [row[0] for row in c.fetchall()]
    conn.close()
    all_known = list(dict.fromkeys(PRESET_PLAYERS + db_players))
    keyboard = []
    row = []
    for name in all_known:
        label = f"✅ {name}" if name in active else name
        row.append(InlineKeyboardButton(label, callback_data=f"ap_{name}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([
        InlineKeyboardButton("➕ Neuer Spieler", callback_data="ap_custom"),
        InlineKeyboardButton("✔️ Fertig", callback_data="ap_done"),
    ])
    return keyboard


def _toggle_player(name: str):
    active = json.loads(db_get("active_players") or "[]")
    conn = sqlite3.connect(DB_FILE)
    conn.execute("INSERT OR IGNORE INTO players (name) VALUES (?)", (name,))
    conn.commit()
    conn.close()
    if name in active:
        active.remove(name)
    else:
        active.append(name)
    db_set("active_players", json.dumps(active))


async def add_player_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        name = " ".join(context.args).strip()
        _toggle_player(name)
        active = json.loads(db_get("active_players") or "[]")
        action = "hinzugefügt" if name in active else "entfernt"
        await update.message.reply_text(
            f"✅ *{name}* {action}! ({len(active)} Spieler)",
            parse_mode="Markdown"
        )
    else:
        active = json.loads(db_get("active_players") or "[]")
        await update.message.reply_text(
            f"👥 *Spieler auswählen*\n\nAktuell: _{', '.join(active) if active else 'niemand'}_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(_build_player_keyboard(active))
        )


async def remove_player(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Verwendung: `/removeplayer Name`", parse_mode="Markdown")
        return
    name = " ".join(context.args).strip()
    active = json.loads(db_get("active_players") or "[]")
    if name not in active:
        await update.message.reply_text(f"❌ {name} nicht dabei.")
        return
    active.remove(name)
    db_set("active_players", json.dumps(active))
    await update.message.reply_text(f"✅ {name} entfernt. Noch {len(active)} Spieler.")


async def list_players(update: Update, context: ContextTypes.DEFAULT_TYPE):
    active = json.loads(db_get("active_players") or "[]")
    if not active:
        await update.message.reply_text("👥 Keine Spieler. /addplayer benutzen.")
        return
    await update.message.reply_text(
        f"👥 *Spieler ({len(active)}):*\n\n" + "\n".join(f"  {i+1}. {p}" for i, p in enumerate(active)),
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────
#  CHIPSET + CALCULATE  (dynamic player count)
# ─────────────────────────────────────────────
async def chipset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(f"🎯 {v['name']}", callback_data=f"chipset_{k}")]
        for k, v in CHIPSETS.items()
    ]
    keyboard.append([InlineKeyboardButton("✏️ Manuell eingeben", callback_data="chipset_manual")])
    await update.message.reply_text(
        "🎯 *Chip-Set auswählen:*", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def set_chips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Format: `/setchips 25:100 100:50 500:20`", parse_mode="Markdown")
        return
    chip_config = {}
    try:
        for item in context.args:
            d, cnt = item.split(":")
            chip_config[str(int(d))] = int(cnt)
    except ValueError:
        await update.message.reply_text("❌ Format: `25:100`", parse_mode="Markdown")
        return
    db_set("chip_config", json.dumps(chip_config))
    await _show_chip_summary(update.message.reply_text, chip_config)


async def _show_chip_summary(reply_fn: Callable, chip_config: dict):
    active = json.loads(db_get("active_players") or "[]")
    buyin_str = db_get("buyin_amount")
    num = len(active)
    total = sum(int(d) * c for d, c in chip_config.items())
    lines = [f"  • {d}er: {c} Stück" for d, c in sorted(chip_config.items(), key=lambda x: int(x[0]))]
    text = "✅ *Chip-Set gespeichert!*\n\n" + "\n".join(lines) + f"\n\nGesamtwert: *{total:,}*"
    if num > 0:
        dist = calculate_chip_distribution(chip_config, num)
        dist_lines = [f"  • {d}er: {c} Stück" for d, c in sorted(dist["chips_per_player"].items(), key=lambda x: int(x[0]))]
        text += f"\n\n🎯 *Pro Spieler ({num}):*\n" + "\n".join(dist_lines)
        text += f"\n💰 Startwert: *{dist['value_per_player']:,}*"
    if buyin_str and num > 0:
        buyin = float(buyin_str)
        pot = buyin * num
        structure = get_payout_structure(num, buyin, pot)
        place_map = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        payout_lines = [f"{place_map[e['place']-1]} Platz {e['place']}: *{e['amount']:.2f}€*" for e in structure]
        text += f"\n\n💰 *Payouts ({num} Spieler, {pot:.0f}€ Pot):*\n" + "\n".join(payout_lines)
    await reply_fn(text, parse_mode="Markdown")


async def calculate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chip_str = db_get("chip_config")
    if not chip_str:
        await update.message.reply_text("❌ Erst /chipset wählen.")
        return
    await _show_chip_summary(update.message.reply_text, json.loads(chip_str))


# ─────────────────────────────────────────────
#  PAYOUT  (dynamic)
# ─────────────────────────────────────────────
async def payout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buyin_str = db_get("buyin_amount")
    if context.args:
        try:
            buyin = float(context.args[0])
            db_set("buyin_amount", str(buyin))
        except ValueError:
            await update.message.reply_text("❌ Ungültiger Betrag.")
            return
    elif buyin_str:
        buyin = float(buyin_str)
    else:
        await update.message.reply_text("💶 Buy-In angeben: `/payout 20`", parse_mode="Markdown")
        return
    active = json.loads(db_get("active_players") or "[]")
    num = len(active) if active else 6
    pot = buyin * num
    structure = get_payout_structure(num, buyin, pot)
    place_map = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    lines = [f"{place_map[e['place']-1]} Platz {e['place']}: *{e['amount']:.2f}€*" for e in structure]
    note = ""
    if num >= 9:
        note = f"\n\n_ℹ️ Platz 4 bekommt exakt den Buy-In ({buyin:.0f}€) zurück_"
    await update.message.reply_text(
        f"💰 *Payouts — {num} Spieler | {buyin:.0f}€ BI | {pot:.0f}€ Pot*\n\n" + "\n".join(lines) + note,
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────
#  BUSTOUT MENU
# ─────────────────────────────────────────────
async def bustout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return
    await _send_bustout_menu(update.message.reply_text)


def _build_bustout_keyboard(remaining: list, busted: list) -> tuple[str, list]:
    place_map = {1: "🥇", 2: "🥈", 3: "🥉"}
    keyboard = [[InlineKeyboardButton(f"💀 {name} ausscheiden", callback_data=f"bust_{name}")] for name in remaining]
    busted_display = ""
    if busted:
        sorted_busted = sorted(busted, key=lambda x: x["place"])
        lines = [f"{place_map.get(b['place'], '#'+str(b['place']))} {b['name']}" for b in sorted_busted]
        busted_display = "\n\nAusgeschieden:\n" + "\n".join(lines)
    keyboard.append([InlineKeyboardButton("🏁 Turnier beenden & auswerten", callback_data="finish_tournament")])
    return busted_display, keyboard


async def _send_bustout_menu(reply_fn: Callable, edit_fn: Optional[Callable] = None):
    players = json.loads(db_get("active_players") or "[]")
    busted = json.loads(db_get("busted_players") or "[]")
    busted_names = {b["name"] for b in busted}
    remaining = [p for p in players if p not in busted_names]
    if not remaining:
        fn = edit_fn or reply_fn
        await fn("✅ Alle ausgeschieden! /endtournament aufrufen.")
        return
    busted_display, keyboard = _build_bustout_keyboard(remaining, busted)
    text = (f"💀 *Bustout-Menü*\n\nNoch im Spiel: *{len(remaining)}* von {len(players)}"
            + busted_display + "\n\nWer scheidet aus?")
    fn = edit_fn or reply_fn
    await fn(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def _handle_bustout(query, name: str, context: ContextTypes.DEFAULT_TYPE):
    players      = json.loads(db_get("active_players") or "[]")
    busted       = json.loads(db_get("busted_players") or "[]")
    busted_names = {b["name"] for b in busted}
    if name not in players:
        await query.answer(f"❌ {name} ist kein aktiver Spieler!")
        return
    if name in busted_names:
        await query.answer(f"⚠️ {name} bereits ausgeschieden!")
        return
    remaining = [p for p in players if p not in busted_names]
    place     = len(remaining)
    busted.append({"name": name, "place": place})
    db_set("busted_players", json.dumps(busted))
    await sync_to_website("running")
    await query.answer(f"💀 {name} — Platz {place}")
    remaining_after = [p for p in players if p not in {b["name"] for b in busted}]

    # Bounty: ask who made the kill
    bounty_str = db_get("bounty_amount")
    if bounty_str and float(bounty_str) > 0 and remaining_after:
        bounty = float(bounty_str)
        kb = [
            [InlineKeyboardButton(
                f"🎯 {p} hat {name} eliminiert (+{bounty:.0f}€)",
                callback_data=f"bounty_{p}_{name}"
            )]
            for p in remaining_after
        ]
        kb.append([InlineKeyboardButton("⏭ Überspringen", callback_data=f"bounty_skip_{name}")])
        await query.edit_message_text(
            f"💀 *{name}* — Platz {place}!\n\n💀 Wer hat *{name}* eliminiert? (+{bounty:.0f}€ Bounty)",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    # Winner?
    if len(remaining_after) == 1:
        winner = remaining_after[0]
        busted.append({"name": winner, "place": 1})
        db_set("busted_players", json.dumps(busted))
        await query.edit_message_text(
            f"🏆 *{winner} GEWINNT!*\n\nLetzter Bustout: 💀 {name} (Platz {place})\n\n_/endtournament für die Auswertung_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏁 Auswertung starten", callback_data="finish_tournament")]])
        )
        return

    # Heads-Up announcement
    if len(remaining_after) == 2:
        chat_id = db_get("main_chat_id")
        if chat_id:
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=f"🤜🤛 *HEADS-UP!*\n\n⚔️ *{remaining_after[0]}* vs *{remaining_after[1]}*\n\n_Möge der Bessere gewinnen!_ 🃏",
                parse_mode="Markdown"
            )

    await _send_bustout_menu(None, edit_fn=query.edit_message_text)


# ─────────────────────────────────────────────
#  END TOURNAMENT  +  REPORTS
# ─────────────────────────────────────────────
async def end_tournament(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return
    await _finalize_tournament(update.message.reply_text, context)


async def _finalize_tournament(reply_fn: Callable, context: ContextTypes.DEFAULT_TYPE):
    db_set("blind_running", "0")
    await sync_to_website("finished")

    players     = json.loads(db_get("active_players") or "[]")
    busted      = json.loads(db_get("busted_players") or "[]")
    buyin       = float(db_get("buyin_amount") or "0")
    bounty      = float(db_get("bounty_amount") or "0")
    bounty_kills = json.loads(db_get("bounty_kills") or "{}")  # {killer: count}

    if not players:
        await reply_fn("❌ Keine aktiven Spieler.")
        return

    # Build placement order
    if busted:
        ordered    = sorted(busted, key=lambda x: x["place"])
        placements = [b["name"] for b in ordered]
        for p in players:
            if p not in placements:
                placements.append(p)
    else:
        placements = list(players)

    num_players = len(players)
    total_pot   = buyin * num_players
    structure   = get_payout_structure(num_players, buyin, total_pot)
    payouts     = {e["place"]: e["amount"] for e in structure}

    conn = sqlite3.connect(DB_FILE)
    c    = conn.cursor()
    c.execute("INSERT INTO tournaments (num_players, buyin_amount, total_pot, chip_config, status, bounty_amount) VALUES (?,?,?,?,?,?)",
              (num_players, buyin, total_pot, db_get("chip_config") or "{}", "finished", bounty))
    tournament_id = c.lastrowid

    place_emoji  = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    result_lines = []
    winner_name   = placements[0]
    winner_payout = payouts.get(1, 0.0)

    for i, name in enumerate(placements):
        place           = i + 1
        payout          = payouts.get(place, 0.0)
        kills           = bounty_kills.get(name, 0)
        bounty_earned   = kills * bounty
        total_payout    = payout + bounty_earned
        profit          = total_payout - buyin - (bounty if bounty > 0 else 0)

        c.execute("INSERT OR IGNORE INTO players (name) VALUES (?)", (name,))
        c.execute("""INSERT INTO results
            (tournament_id, player_name, place, payout, bounties_won, bounty_payout)
            VALUES (?,?,?,?,?,?)""",
                  (tournament_id, name, place, payout, kills, bounty_earned))

        # Update player stats + streak
        won = 1 if place == 1 else 0
        c.execute("SELECT current_streak, best_streak, worst_streak FROM players WHERE name=?", (name,))
        streak_row = c.fetchone()
        cur_streak = streak_row[0] if streak_row else 0
        best       = streak_row[1] if streak_row else 0
        worst      = streak_row[2] if streak_row else 0

        if won:
            new_streak = cur_streak + 1 if cur_streak >= 0 else 1
        else:
            new_streak = cur_streak - 1 if cur_streak <= 0 else -1

        new_best  = max(best, new_streak)
        new_worst = min(worst, new_streak)

        c.execute("""UPDATE players SET
            total_tournaments=total_tournaments+1,
            total_wins=total_wins+?,
            total_earnings=total_earnings+?,
            total_buyins=total_buyins+?,
            current_streak=?,
            best_streak=?,
            worst_streak=?
            WHERE name=?""",
                  (won, total_payout, buyin + (bounty if bounty > 0 else 0),
                   new_streak, new_best, new_worst, name))

        emoji = place_emoji[i] if i < len(place_emoji) else f"{place}."
        line  = f"{emoji} *{name}* — {payout:.2f}€"
        if bounty_earned > 0:
            line += f" + {bounty_earned:.0f}€ Bounty ({kills}x 💀)"
        profit_val = payout + bounty_earned - buyin - (bounty if bounty > 0 else 0)
        line += f" ({'+'if profit_val>=0 else ''}{profit_val:.2f}€)"
        result_lines.append(line)

        # Streak badge
        if new_streak >= 3:
            result_lines[-1] += f" 🔥{new_streak}"
        elif new_streak <= -3:
            result_lines[-1] += f" 🥶{abs(new_streak)}"

    conn.commit()
    conn.close()

    winner_bounty = bounty_kills.get(winner_name, 0) * bounty
    winner_profit = winner_payout + winner_bounty - buyin - (bounty if bounty > 0 else 0)
    winner_roi    = (winner_profit / (buyin + bounty) * 100) if (buyin + bounty) > 0 else 0

    bounty_line = f"💀 Bounty-Modus: {bounty:.0f}€ pro Kill\n" if bounty > 0 else ""

    summary = (
        f"🏆 *TURNIER #{tournament_id} BEENDET!*\n\n"
        f"👑 *SIEGER: {winner_name}*\n"
        f"💰 Gewinn: *{winner_payout:.2f}€*"
        + (f" + {winner_bounty:.0f}€ Bounties" if winner_bounty > 0 else "") +
        f"  |  Buy-In: {buyin:.2f}€\n"
        f"📈 ROI heute: *+{winner_roi:.0f}%*\n\n"
        + bounty_line +
        f"📋 *Ergebnis:*\n" + "\n".join(result_lines) +
        f"\n\n💵 Pot: {total_pot:.2f}€  |  {num_players} Spieler"
    )
    await reply_fn(summary, parse_mode="Markdown")

    chat_id = db_get("main_chat_id")
    if chat_id:
        chart = _generate_profit_chart(placements, payouts, buyin, tournament_id, bounty_kills, bounty)
        if chart:
            with open(chart, "rb") as f:
                await context.bot.send_photo(
                    chat_id=int(chat_id), photo=f,
                    caption=f"📊 Turnier #{tournament_id} — Profit/Verlust je Spieler"
                )
            os.remove(chart)

    await _send_player_summaries(context, placements, payouts, buyin, tournament_id, bounty_kills, bounty)
    clear_session()


# ─────────────────────────────────────────────
#  CHARTS
# ─────────────────────────────────────────────
def _generate_profit_chart(placements, payouts, buyin, tournament_id, bounty_kills=None, bounty=0.0) -> Optional[str]:
    try:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    if bounty_kills is None:
        bounty_kills = {}
    profits = [payouts.get(i+1,0.0) + bounty_kills.get(n,0)*bounty - buyin - (bounty if bounty>0 else 0)
               for i, n in enumerate(placements)]
    colors  = ["#2ecc71" if p >= 0 else "#e74c3c" for p in profits]
    fig, ax = plt.subplots(figsize=(max(8, len(placements) * 1.3), 5))
    fig.patch.set_facecolor("#1a1a2e"); ax.set_facecolor("#16213e")
    bars = ax.bar(placements, profits, color=colors, edgecolor="#0f3460", linewidth=1.5, width=0.6)
    for bar, p in zip(bars, profits):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + (0.5 if p >= 0 else -1.5),
                f"+{p:.0f}€" if p >= 0 else f"{p:.0f}€",
                ha="center", va="bottom", fontsize=10, fontweight="bold", color="#f0f0f0")
    ax.axhline(0, color="#aaaaaa", linewidth=0.8, linestyle="--")
    ax.set_title(f"🃏 Turnier #{tournament_id} — Profit/Verlust", color="#f0f0f0", fontsize=13, fontweight="bold")
    ax.set_ylabel("€", color="#aaaaaa"); ax.tick_params(colors="#cccccc")
    for s in ["top","right"]: ax.spines[s].set_visible(False)
    for s in ["left","bottom"]: ax.spines[s].set_color("#444466")
    plt.xticks(rotation=20 if len(placements) > 5 else 0); plt.tight_layout()
    path = f"/tmp/poker_chart_{tournament_id}.png"
    plt.savefig(path, dpi=150, facecolor=fig.get_facecolor()); plt.close()
    return path


def _generate_player_chart(name: str, cum_data: list) -> Optional[str]:
    try:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    fig, ax = plt.subplots(figsize=(8, 4))
    fig.patch.set_facecolor("#1a1a2e"); ax.set_facecolor("#16213e")
    x = list(range(1, len(cum_data) + 1))
    color = "#2ecc71" if cum_data[-1] >= 0 else "#e74c3c"
    ax.plot(x, cum_data, color=color, linewidth=2.5, marker="o", markersize=6, markerfacecolor="white")
    ax.fill_between(x, cum_data, 0, alpha=0.15, color=color)
    ax.axhline(0, color="#aaaaaa", linewidth=0.8, linestyle="--")
    last = cum_data[-1]
    ax.annotate(f"+{last:.0f}€" if last >= 0 else f"{last:.0f}€",
                xy=(x[-1], last), xytext=(8, 0), textcoords="offset points", color="white", fontsize=11, fontweight="bold")
    ax.set_title(f"📈 {name} — Kumulativer Profit", color="#f0f0f0", fontsize=13, fontweight="bold")
    ax.set_xlabel("Turnier #", color="#aaaaaa"); ax.set_ylabel("€", color="#aaaaaa")
    ax.tick_params(colors="#cccccc"); ax.xaxis.set_major_locator(plt.MaxNLocator(integer=True))
    for s in ["top","right"]: ax.spines[s].set_visible(False)
    for s in ["left","bottom"]: ax.spines[s].set_color("#444466")
    plt.tight_layout()
    path = f"/tmp/poker_player_{name.replace(' ','_')}.png"
    plt.savefig(path, dpi=150, facecolor=fig.get_facecolor()); plt.close()
    return path


async def _send_player_summaries(context, placements, payouts, buyin, tournament_id, bounty_kills=None, bounty=0.0):
    if bounty_kills is None:
        bounty_kills = {}
    chat_id = db_get("main_chat_id")
    if not chat_id:
        return
    conn = sqlite3.connect(DB_FILE)
    c    = conn.cursor()
    place_map = {1: "🥇", 2: "🥈", 3: "🥉"}
    for i, name in enumerate(placements):
        place         = i + 1
        payout        = payouts.get(place, 0.0)
        kills         = bounty_kills.get(name, 0)
        bounty_earned = kills * bounty
        profit        = payout + bounty_earned - buyin - (bounty if bounty > 0 else 0)
        c.execute("SELECT total_tournaments, total_wins, total_earnings, total_buyins, current_streak, best_streak, worst_streak FROM players WHERE name=?", (name,))
        row = c.fetchone()
        if not row:
            continue
        t, w, e, b, cur_streak, best_streak, worst_streak = row
        total_profit = e - b
        roi      = (total_profit / b * 100) if b > 0 else 0
        win_rate = (w / t * 100) if t > 0 else 0
        emoji    = place_map.get(place, f"#{place}")

        # Streak display
        if cur_streak > 0:
            streak_line = f"🔥 {cur_streak} Siege in Folge!"
        elif cur_streak < 0:
            streak_line = f"🥶 {abs(cur_streak)} Niederlagen in Folge"
        else:
            streak_line = "➖ Streak: 0"

        bounty_line = f"\n├ Bounties: {kills}x 💀 = +{bounty_earned:.0f}€" if kills > 0 else ""

        await context.bot.send_message(chat_id=int(chat_id), parse_mode="Markdown", text=(
            f"{emoji} *{name}* — Turnier #{tournament_id}\n"
            f"├ Platz {place} von {len(placements)}\n"
            f"├ Payout: {payout:.2f}€  (Buy-In: {buyin:.2f}€)"
            + bounty_line +
            f"\n└ Heute: *{'+'if profit>=0 else ''}{profit:.2f}€*\n\n"
            f"📊 *Gesamtbilanz:*\n"
            f"├ {t} Turniere  |  {w} Siege  ({win_rate:.0f}% WR)\n"
            f"├ Profit: *{'+'if total_profit>=0 else ''}{total_profit:.2f}€*  |  ROI: *{'+'if roi>=0 else ''}{roi:.0f}%*\n"
            f"├ Best Streak: 🔥{best_streak}  |  Worst: 🥶{abs(worst_streak)}\n"
            f"└ Aktuell: {streak_line}"
        ))
        await asyncio.sleep(0.4)
    conn.close()


# ─────────────────────────────────────────────
#  STATS
# ─────────────────────────────────────────────
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_FILE)
    c    = conn.cursor()
    c.execute("""SELECT name, total_tournaments, total_wins, total_earnings, total_buyins
                 FROM players WHERE total_tournaments>0
                 ORDER BY (total_earnings-total_buyins) DESC LIMIT 15""")
    rows = c.fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("📊 Noch keine Daten.")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines  = []
    for i, (name, t, w, e, b) in enumerate(rows):
        profit = e - b
        roi    = (profit / b * 100) if b > 0 else 0
        medal  = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{medal} *{name}*\n   🎮 {t}x  🏆 {w}x  💰 {'+'if profit>=0 else ''}{profit:.0f}€  📈 {'+'if roi>=0 else ''}{roi:.0f}%")
    await update.message.reply_text("📊 *Leaderboard*\n\n" + "\n\n".join(lines), parse_mode="Markdown")


async def player_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Verwendung: `/playerstats Dominik`", parse_mode="Markdown")
        return
    name = " ".join(context.args).strip()
    conn = sqlite3.connect(DB_FILE)
    c    = conn.cursor()
    c.execute("SELECT * FROM players WHERE name=?", (name,))
    player = c.fetchone()
    if not player:
        await update.message.reply_text(f"❌ '{name}' nicht gefunden.")
        conn.close()
        return
    _, pname, _, t, w, e, b = player[:7]
    cur_streak   = player[7] if len(player) > 7 else 0
    best_streak  = player[8] if len(player) > 8 else 0
    worst_streak = player[9] if len(player) > 9 else 0
    profit   = e - b
    roi      = (profit / b * 100) if b > 0 else 0
    win_rate = (w / t * 100) if t > 0 else 0
    c.execute("""SELECT t.date, r.place, r.payout, t.buyin_amount
                 FROM results r JOIN tournaments t ON r.tournament_id=t.id
                 WHERE r.player_name=? ORDER BY t.date ASC""", (name,))
    all_results = c.fetchall()
    conn.close()
    cum = 0.0; cum_data = []; recent_lines = []
    place_map = {1: "🥇", 2: "🥈", 3: "🥉"}
    for date, place, payout, bi in all_results:
        p = payout - bi; cum += p; cum_data.append(cum)
        recent_lines.append(f"  {place_map.get(place, str(place)+'.')} {date[:10]} Platz {place}  {'+'if p>=0 else ''}{p:.0f}€")
    profit_str = f"+{profit:.2f}€" if profit >= 0 else f"{profit:.2f}€"
    streak_icon = "🔥" if cur_streak > 0 else ("🥶" if cur_streak < 0 else "➖")
    streak_val  = abs(cur_streak)
    text = (
        f"👤 *{pname}*\n\n"
        f"🎮 {t} Turniere  |  🏆 {w} Siege  ({win_rate:.0f}% WR)\n"
        f"💰 Profit: *{profit_str}*  |  ROI: *{'+'if roi>=0 else ''}{roi:.0f}%*\n"
        f"📊 Einnahmen: {e:.2f}€  |  Kosten: {b:.2f}€\n\n"
        f"🏅 *Streaks:*\n"
        f"  Aktuell: {streak_icon} {streak_val} {'Siege' if cur_streak >= 0 else 'Niederlagen'}\n"
        f"  Best: 🔥{best_streak}  |  Worst: 🥶{abs(worst_streak)}"
    )
    if recent_lines:
        text += "\n\n📋 *Historie:*\n" + "\n".join(recent_lines[-10:])
    await update.message.reply_text(text, parse_mode="Markdown")
    if len(cum_data) >= 2:
        chart = _generate_player_chart(pname, cum_data)
        if chart:
            with open(chart, "rb") as f:
                await update.message.reply_photo(photo=f, caption=f"📈 {pname} — Kumulativer Profit")
            os.remove(chart)


# ─────────────────────────────────────────────
#  SHOT CLOCK
# ─────────────────────────────────────────────
async def shotclock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return
    db_set("current_blind_level", "1")
    db_set("blind_start_time", datetime.now().isoformat())
    db_set("blind_running", "1")
    await sync_to_website("running")
    level    = get_active_blind_levels()[0]
    end_time = (datetime.now() + timedelta(minutes=level["minutes"])).strftime("%H:%M")
    keyboard = [[
        InlineKeyboardButton("⏭ Nächstes Level", callback_data="next_level"),
        InlineKeyboardButton("⏹ Stoppen", callback_data="stop_blind"),
    ],[
        InlineKeyboardButton("⏱ Zeit prüfen", callback_data="time_left"),
        InlineKeyboardButton("📋 Alle Level", callback_data="all_levels"),
    ]]
    await update.message.reply_text(
        f"⏱ *Shot Clock gestartet!*\n\n*Level {level['level']}*\n"
        f"🔵 Small: {level['small']:,}  🔴 Big: {level['big']:,}\n"
        f"⏰ {level['minutes']} min  |  Ende: {end_time}",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def _advance_level(reply_fn: Callable):
    levels  = get_active_blind_levels()
    current = int(db_get("current_blind_level") or "1")
    nxt     = current + 1
    if nxt > len(levels):
        await reply_fn("🏁 Letztes Blind-Level erreicht!")
        return
    db_set("current_blind_level", str(nxt))
    db_set("blind_start_time", datetime.now().isoformat())
    await sync_to_website("running")
    level    = levels[nxt - 1]
    prev     = levels[current - 1]
    end_time = (datetime.now() + timedelta(minutes=level["minutes"])).strftime("%H:%M")
    keyboard = [[
        InlineKeyboardButton("⏭ Nächstes Level", callback_data="next_level"),
        InlineKeyboardButton("⏱ Zeit", callback_data="time_left"),
    ],[
        InlineKeyboardButton("📋 Alle Level", callback_data="all_levels"),
    ]]
    await reply_fn(
        f"⏭ *Level {nxt} — Blinds erhöht!*\n\n"
        f"War: {prev['small']:,}/{prev['big']:,}\n"
        f"Jetzt: *{level['small']:,}/{level['big']:,}*\n"
        f"⏰ {level['minutes']} min  |  Ende: {end_time}",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def next_level_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _advance_level(update.message.reply_text)


async def stop_blind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_set("blind_running", "0")
    await update.message.reply_text("⏹ Blind-Timer gestoppt.")


async def blinds_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if db_get("blind_running") != "1":
        await update.message.reply_text("⏹ Kein aktiver Timer. /shotclock zum Starten.")
        return
    levels   = get_active_blind_levels()
    current  = int(db_get("current_blind_level") or "1")
    level    = levels[current - 1]
    elapsed  = datetime.now() - datetime.fromisoformat(db_get("blind_start_time"))
    remaining = max(timedelta(minutes=level["minutes"]) - elapsed, timedelta(0))
    mins, secs = divmod(int(remaining.total_seconds()), 60)
    next_info = f"\n_Nächstes: {levels[current]['small']:,}/{levels[current]['big']:,}_" if current < len(levels) else ""
    keyboard = [[
        InlineKeyboardButton("⏭ Nächstes Level", callback_data="next_level"),
        InlineKeyboardButton("🔄 Aktualisieren", callback_data="time_left"),
    ]]
    await update.message.reply_text(
        f"⏱ *Level {current}*\n\n🔵 {level['small']:,}  🔴 {level['big']:,}\n⏰ Verbleibend: *{mins}:{secs:02d}*" + next_info,
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def blind_structure_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    levels  = get_active_blind_levels()
    current = int(db_get("current_blind_level") or "0")
    lines   = [
        f"Lvl {i+1:2d} | {l['small']:>5,}/{l['big']:>6,} | {l['minutes']:2d}min"
        + (" ◀️" if i + 1 == current else "")
        for i, l in enumerate(levels)
    ]
    await update.message.reply_text("📋 *Blind-Struktur:*\n\n`" + "\n".join(lines) + "`", parse_mode="Markdown")


# ─────────────────────────────────────────────
#  STATUS
# ─────────────────────────────────────────────
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    active   = json.loads(db_get("active_players") or "[]")
    busted   = json.loads(db_get("busted_players") or "[]")
    remaining = [p for p in active if p not in {b["name"] for b in busted}]
    buyin_str = db_get("buyin_amount")
    blind_running = db_get("blind_running")
    current_blind = db_get("current_blind_level")
    text = "📊 *Turnierstatus*\n\n"
    text += f"👥 Spieler: {len(active)} ({', '.join(active) if active else 'keine'})\n"
    text += f"🃏 Im Spiel: {len(remaining)} ({', '.join(remaining) if remaining else '—'})\n"
    if buyin_str:
        b = float(buyin_str)
        text += f"💶 Buy-In: {b:.0f}€  |  Pot: {b*len(active):.0f}€\n"
    if blind_running == "1" and current_blind:
        lvl = get_active_blind_levels()[int(current_blind) - 1]
        text += f"⏱ Level {current_blind}: {lvl['small']:,}/{lvl['big']:,} ▶️\n"
    else:
        text += "⏱ Blind-Timer: gestoppt\n"
    await update.message.reply_text(text, parse_mode="Markdown")


# ─────────────────────────────────────────────
#  ADMIN PANEL
# ─────────────────────────────────────────────
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return
    keyboard = [
        [InlineKeyboardButton("👥 Admins verwalten", callback_data="adm_list_admins")],
        [InlineKeyboardButton("🗑 Turnier löschen", callback_data="adm_del_tournament"),
         InlineKeyboardButton("📥 Altes Turnier", callback_data="adm_add_history")],
        [InlineKeyboardButton("💾 DB exportieren", callback_data="adm_export_db"),
         InlineKeyboardButton("📤 DB importieren", callback_data="adm_import_db")],
        [InlineKeyboardButton("🔄 Stats zurücksetzen", callback_data="adm_reset_stats")],
        [InlineKeyboardButton("🧹 Session leeren", callback_data="adm_clear_session")],
    ]
    await update.message.reply_text(
        "🔑 *Admin-Panel*", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def add_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_superadmin(update):
        return
    if len(context.args) < 2:
        await update.message.reply_text("Verwendung: `/addadmin 123456789 Username`", parse_mode="Markdown")
        return
    try:
        uid   = int(context.args[0])
        uname = context.args[1]
    except ValueError:
        await update.message.reply_text("❌ Ungültige ID.")
        return
    conn = sqlite3.connect(DB_FILE)
    conn.execute("INSERT OR REPLACE INTO admins (user_id, username, role) VALUES (?,?,?)", (uid, uname, "admin"))
    conn.commit(); conn.close()
    await update.message.reply_text(f"✅ *{uname}* ({uid}) ist jetzt Admin!", parse_mode="Markdown")


async def remove_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_superadmin(update):
        return
    if not context.args:
        await update.message.reply_text("Verwendung: `/removeadmin 123456789`", parse_mode="Markdown")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Ungültige ID.")
        return
    conn = sqlite3.connect(DB_FILE)
    conn.execute("DELETE FROM admins WHERE user_id=?", (uid,))
    conn.commit(); conn.close()
    await update.message.reply_text(f"✅ Admin {uid} entfernt.")


# ─────────────────────────────────────────────
#  DB EXPORT / IMPORT
# ─────────────────────────────────────────────
async def export_db_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return
    if not os.path.exists(DB_FILE):
        await update.message.reply_text("❌ Keine Datenbank gefunden.")
        return
    with open(DB_FILE, "rb") as f:
        await update.message.reply_document(
            document=f,
            filename=f"poker_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.db",
            caption="💾 Datenbank-Backup"
        )


async def import_db_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Usage: Send a .db file to the chat with caption /importdb
    Or: /importdb (then send file in next message)
    """
    if not await require_admin(update):
        return

    # Check if a document was attached to this message
    if update.message.document:
        await _process_db_import(update, context, update.message.document)
        return

    # No file attached — set waiting state and instruct user
    db_set("waiting_for_db_import", str(update.effective_user.id))
    await update.message.reply_text(
        "📤 *Datenbank importieren*\n\n"
        "Sende jetzt die `.db` Datei als Dateianhang in diesen Chat.\n\n"
        "⚠️ *Achtung: Die aktuelle Datenbank wird überschrieben!*\n"
        "_Ein Backup wird automatisch erstellt._",
        parse_mode="Markdown"
    )


async def _process_db_import(update: Update, context: ContextTypes.DEFAULT_TYPE, document: Document):
    if not await require_admin(update):
        return
    if not document.file_name or not document.file_name.endswith(".db"):
        await update.message.reply_text("❌ Bitte eine `.db` Datei senden.")
        return

    await update.message.reply_text("⏳ Importiere Datenbank...")

    # Backup current DB
    if os.path.exists(DB_FILE):
        backup_path = DB_FILE + f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(DB_FILE, backup_path)

    # Download and replace
    try:
        file = await context.bot.get_file(document.file_id)
        await file.download_to_drive(DB_FILE)
        init_db()  # Ensure new tables exist if old DB was from earlier version
        db_del("waiting_for_db_import")

        # Quick sanity check
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM players")
        player_count = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM tournaments")
        tournament_count = c.fetchone()[0]
        conn.close()

        await update.message.reply_text(
            f"✅ *Datenbank erfolgreich importiert!*\n\n"
            f"👥 {player_count} Spieler\n"
            f"🎮 {tournament_count} Turniere\n\n"
            f"_Backup der alten DB wurde erstellt._",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Import fehlgeschlagen: {e}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming document files — check if DB import is pending."""
    waiting_uid = db_get("waiting_for_db_import")
    if waiting_uid and int(waiting_uid) == update.effective_user.id:
        if update.message.document:
            await _process_db_import(update, context, update.message.document)
            return
    # Save chat ID for all other messages
    chat_id = str(update.effective_chat.id)
    if db_get("main_chat_id") != chat_id:
        db_set("main_chat_id", chat_id)


# ─────────────────────────────────────────────
#  HISTORY MANAGEMENT
# ─────────────────────────────────────────────
async def admin_add_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /addhistory DATUM BUYIN 1:Name 2:Name 3:Name ...
    Example: /addhistory 2024-03-15 20 1:Dominik 2:Alex 3:Max 4:Jonas
    """
    if not await require_admin(update):
        return
    if len(context.args) < 4:
        await update.message.reply_text(
            "📥 *Altes Turnier eintragen:*\n\n"
            "`/addhistory DATUM BUYIN 1:Name 2:Name ...`\n\n"
            "Beispiel:\n`/addhistory 2024-03-15 20 1:Dominik 2:Alex 3:Max 4:Jonas`\n\n"
            "Datum-Format: JJJJ-MM-TT",
            parse_mode="Markdown"
        )
        return
    try:
        date_str = context.args[0]
        buyin    = float(context.args[1])
        placements: dict[int, str] = {}
        for item in context.args[2:]:
            place_str, name = item.split(":", 1)
            placements[int(place_str)] = name
        num_players = len(placements)
        total_pot   = buyin * num_players
        structure   = get_payout_structure(num_players, buyin, total_pot)
        payouts     = {e["place"]: e["amount"] for e in structure}

        conn = sqlite3.connect(DB_FILE)
        c    = conn.cursor()
        c.execute("INSERT INTO tournaments (date, num_players, buyin_amount, total_pot, chip_config, status) VALUES (?,?,?,?,?,?)",
                  (date_str + " 20:00:00", num_players, buyin, total_pot, "{}", "finished"))
        tournament_id = c.lastrowid
        place_emoji   = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]
        lines = []
        for place in sorted(placements.keys()):
            name   = placements[place]
            payout = payouts.get(place, 0.0)
            c.execute("INSERT OR IGNORE INTO players (name) VALUES (?)", (name,))
            c.execute("INSERT INTO results (tournament_id, player_name, place, payout) VALUES (?,?,?,?)",
                      (tournament_id, name, place, payout))
            c.execute("""UPDATE players SET total_tournaments=total_tournaments+1, total_wins=total_wins+?,
                total_earnings=total_earnings+?, total_buyins=total_buyins+? WHERE name=?""",
                      (1 if place == 1 else 0, payout, buyin, name))
            emoji  = place_emoji[place - 1] if place <= len(place_emoji) else f"{place}."
            profit = payout - buyin
            lines.append(f"{emoji} {name} — {payout:.2f}€ ({'+'if profit>=0 else ''}{profit:.2f}€)")
        conn.commit(); conn.close()
        await update.message.reply_text(
            f"✅ *Turnier #{tournament_id} eingetragen ({date_str})*\n\n" + "\n".join(lines),
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Fehler: {e}\nFormat prüfen!")


async def admin_delete_tournament(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_superadmin(update):
        return
    if not context.args:
        conn = sqlite3.connect(DB_FILE)
        c    = conn.cursor()
        c.execute("SELECT id, date, num_players, buyin_amount FROM tournaments ORDER BY id DESC LIMIT 10")
        rows = c.fetchall()
        conn.close()
        if not rows:
            await update.message.reply_text("Keine Turniere.")
            return
        lines = [f"  #{t_id} | {date[:10]} | {np} Spieler | {bi:.0f}€" for t_id, date, np, bi in rows]
        await update.message.reply_text(
            "🗑 *Letzte Turniere:*\n\n`" + "\n".join(lines) + "`\n\nLöschen: `/deletetournament ID`",
            parse_mode="Markdown")
        return
    try:
        t_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Ungültige ID.")
        return
    conn = sqlite3.connect(DB_FILE)
    c    = conn.cursor()
    c.execute("SELECT buyin_amount FROM tournaments WHERE id=?", (t_id,))
    row = c.fetchone()
    if not row:
        await update.message.reply_text(f"❌ #{t_id} nicht gefunden.")
        conn.close()
        return
    buyin = row[0]
    c.execute("SELECT player_name, place, payout FROM results WHERE tournament_id=?", (t_id,))
    for name, place, payout in c.fetchall():
        c.execute("""UPDATE players SET total_tournaments=total_tournaments-1, total_wins=total_wins-?,
            total_earnings=total_earnings-?, total_buyins=total_buyins-? WHERE name=?""",
                  (1 if place == 1 else 0, payout, buyin, name))
    c.execute("DELETE FROM results WHERE tournament_id=?", (t_id,))
    c.execute("DELETE FROM tournaments WHERE id=?", (t_id,))
    conn.commit(); conn.close()
    await update.message.reply_text(f"✅ Turnier #{t_id} gelöscht + Statistiken korrigiert.")


async def admin_reset_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_superadmin(update):
        return
    keyboard = [[
        InlineKeyboardButton("⚠️ JA, alles löschen", callback_data="confirm_reset_stats"),
        InlineKeyboardButton("❌ Abbrechen", callback_data="cancel_reset"),
    ]]
    await update.message.reply_text(
        "⚠️ *Alle Statistiken löschen?*", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ─────────────────────────────────────────────
#  BLIND BACKGROUND JOB
# ─────────────────────────────────────────────
async def check_blind_timer(context: ContextTypes.DEFAULT_TYPE):
    if db_get("blind_running") != "1":
        return
    current_str = db_get("current_blind_level")
    start_str   = db_get("blind_start_time")
    if not current_str or not start_str:
        return
    await sync_to_website("running")
    levels  = get_active_blind_levels()
    current = int(current_str)
    if current > len(levels):
        return
    level   = levels[current - 1]
    elapsed = datetime.now() - datetime.fromisoformat(start_str)
    dur     = timedelta(minutes=level["minutes"])

    warned = db_get(f"warned_{current}")
    if elapsed >= dur - timedelta(minutes=2) and not warned:
        db_set(f"warned_{current}", "1")
        chat_id = db_get("main_chat_id")
        if chat_id:
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=f"⚠️ *Noch 2 Minuten — Level {current}!*\n{level['small']:,}/{level['big']:,}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Level wechseln", callback_data="next_level")]])
            )

    if elapsed >= dur:
        nxt = current + 1
        if nxt <= len(levels):
            db_set("current_blind_level", str(nxt))
            db_set("blind_start_time", datetime.now().isoformat())
            db_del(f"warned_{current}")
            next_level = levels[nxt - 1]
            chat_id    = db_get("main_chat_id")
            if chat_id:
                await context.bot.send_message(
                    chat_id=int(chat_id),
                    text=(f"🔔 *BLINDS ERHÖHT — Level {nxt}!*\n\n"
                          f"🔵 Small: *{next_level['small']:,}*\n"
                          f"🔴 Big: *{next_level['big']:,}*\n⏰ {next_level['minutes']} min"),
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("⏭ Weiter", callback_data="next_level"),
                        InlineKeyboardButton("⏱ Zeit", callback_data="time_left"),
                    ]])
                )


# ─────────────────────────────────────────────
#  CALLBACK HANDLER
# ─────────────────────────────────────────────
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data  = query.data

    if data.startswith("bust_"):
        await _handle_bustout(query, data[5:], context)
        return

    # Bounty kill attribution
    if data.startswith("bounty_"):
        parts = data[7:].split("_", 1)
        if parts[0] == "skip":
            # No killer — just continue
            busted_name = parts[1] if len(parts) > 1 else ""
            players     = json.loads(db_get("active_players") or "[]")
            busted      = json.loads(db_get("busted_players") or "[]")
            remaining   = [p for p in players if p not in {b["name"] for b in busted}]
            if len(remaining) == 1:
                winner = remaining[0]
                busted.append({"name": winner, "place": 1})
                db_set("busted_players", json.dumps(busted))
                await query.edit_message_text(
                    f"🏆 *{winner} GEWINNT!*\n\n_/endtournament für die Auswertung_",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏁 Auswertung starten", callback_data="finish_tournament")]])
                )
            else:
                await _send_bustout_menu(None, edit_fn=query.edit_message_text)
            return
        # killer_name _ busted_name
        killer      = parts[0]
        busted_name = parts[1] if len(parts) > 1 else ""
        kills       = json.loads(db_get("bounty_kills") or "{}")
        kills[killer] = kills.get(killer, 0) + 1
        db_set("bounty_kills", json.dumps(kills))
        bounty = float(db_get("bounty_amount") or "0")
        await query.answer(f"🎯 {killer} bekommt {bounty:.0f}€ Bounty!")

        # Check if game is over
        players   = json.loads(db_get("active_players") or "[]")
        busted    = json.loads(db_get("busted_players") or "[]")
        remaining = [p for p in players if p not in {b["name"] for b in busted}]
        if len(remaining) == 1:
            winner = remaining[0]
            busted.append({"name": winner, "place": 1})
            db_set("busted_players", json.dumps(busted))
            winner_kills        = kills.get(winner, 0)
            winner_bounty_total = winner_kills * bounty
            await query.edit_message_text(
                f"🏆 *{winner} GEWINNT!*\n"
                + (f"💀 {winner_kills} Bounties = +{winner_bounty_total:.0f}€\n" if winner_kills > 0 else "")
                + "\n_/endtournament für die Auswertung_",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏁 Auswertung starten", callback_data="finish_tournament")]])
            )
        else:
            await _send_bustout_menu(None, edit_fn=query.edit_message_text)
        return

    if data == "finish_tournament":
        await query.edit_message_text("🏁 Turnier wird ausgewertet...")
        await _finalize_tournament(
            lambda text, **kw: context.bot.send_message(chat_id=query.message.chat_id, text=text, **kw),
            context
        )
        return

    if data == "next_level":
        await _advance_level(lambda text, **kw: query.edit_message_text(text, **kw))
        return

    if data == "stop_blind":
        db_set("blind_running", "0")
        await query.edit_message_text("⏹ Blind-Timer gestoppt.")
        return

    if data == "time_left":
        levels   = get_active_blind_levels()
        current  = int(db_get("current_blind_level") or "1")
        level    = levels[current - 1]
        start    = db_get("blind_start_time")
        elapsed  = datetime.now() - datetime.fromisoformat(start) if start else timedelta(0)
        remaining = max(timedelta(minutes=level["minutes"]) - elapsed, timedelta(0))
        mins, secs = divmod(int(remaining.total_seconds()), 60)
        await query.edit_message_text(
            f"⏱ *Level {current}*\n\n🔵 {level['small']:,}  🔴 {level['big']:,}\n⏰ Verbleibend: *{mins}:{secs:02d}*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⏭ Nächstes Level", callback_data="next_level"),
                InlineKeyboardButton("🔄 Aktualisieren", callback_data="time_left"),
            ]])
        )
        return

    if data == "all_levels":
        levels  = get_active_blind_levels()
        current = int(db_get("current_blind_level") or "0")
        lines   = [
            f"Lvl {i+1:2d} | {l['small']:>5,}/{l['big']:>6,} | {l['minutes']:2d}min"
            + (" ◀️" if i + 1 == current else "")
            for i, l in enumerate(levels)
        ]
        keyboard = [[InlineKeyboardButton("🔙 Zurück", callback_data="time_left")]]
        await query.edit_message_text(
            "📋 *Blind-Struktur:*\n\n`" + "\n".join(lines) + "`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # Player menu
    if data.startswith("ap_"):
        action = data[3:]
        if action == "done":
            active = json.loads(db_get("active_players") or "[]")
            buyin_str = db_get("buyin_amount")
            info = ""
            if buyin_str and active:
                buyin = float(buyin_str)
                pot   = buyin * len(active)
                structure = get_payout_structure(len(active), buyin, pot)
                place_map = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
                payout_lines = [f"{place_map[e['place']-1]} Platz {e['place']}: {e['amount']:.2f}€" for e in structure]
                info = f"\n\n💰 *Payouts ({len(active)} Spieler, {pot:.0f}€ Pot):*\n" + "\n".join(payout_lines)
            await query.edit_message_text(
                f"✅ *{len(active)} Spieler:* {', '.join(active)}" + info + "\n\nNächster Schritt: /chipset",
                parse_mode="Markdown"
            )
            return
        if action == "custom":
            await query.edit_message_text("✏️ Neuen Spieler: `/addplayer Name`", parse_mode="Markdown")
            return
        _toggle_player(action)
        active = json.loads(db_get("active_players") or "[]")
        await query.edit_message_text(
            f"👥 *Spieler auswählen*\n\nAktuell: _{', '.join(active) if active else 'niemand'}_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(_build_player_keyboard(active))
        )
        return

    # Chipset
    if data.startswith("chipset_"):
        key = data[8:]
        if key == "manual":
            await query.edit_message_text("✏️ `/setchips 25:100 100:50 500:20 1000:10`", parse_mode="Markdown")
            return
        if key in CHIPSETS:
            chip_config = {str(k): v for k, v in CHIPSETS[key]["chips"].items()}
            db_set("chip_config", json.dumps(chip_config))
            await _show_chip_summary(lambda text, **kw: query.edit_message_text(text, **kw), chip_config)
        return

    # Admin callbacks
    if data == "adm_clear_session":
        clear_session()
        await query.edit_message_text("🧹 Session geleert. /newgame für neues Turnier.")
        return

    if data == "adm_reset_stats":
        await query.edit_message_text(
            "⚠️ *Alle Statistiken löschen?*", parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⚠️ JA", callback_data="confirm_reset_stats"),
                InlineKeyboardButton("❌ Abbrechen", callback_data="cancel_reset"),
            ]])
        )
        return

    if data == "confirm_reset_stats":
        if not is_superadmin(query.from_user.id):
            await query.edit_message_text("⛔ Nur Superadmins.")
            return
        conn = sqlite3.connect(DB_FILE)
        conn.execute("DELETE FROM results")
        conn.execute("DELETE FROM tournaments")
        conn.execute("UPDATE players SET total_tournaments=0,total_wins=0,total_earnings=0,total_buyins=0")
        conn.commit(); conn.close()
        await query.edit_message_text("✅ Alle Statistiken zurückgesetzt.")
        return

    if data == "cancel_reset":
        await query.edit_message_text("❌ Abgebrochen.")
        return

    if data == "adm_list_admins":
        supers    = [f"👑 Superadmin (ENV): {uid}" for uid in SUPERADMIN_IDS]
        conn      = sqlite3.connect(DB_FILE)
        c         = conn.cursor()
        c.execute("SELECT user_id, username FROM admins")
        db_admins = [f"🔑 {un} ({uid})" for uid, un in c.fetchall()]
        conn.close()
        text = "*Admins:*\n\n" + "\n".join(supers + db_admins) if (supers or db_admins) else "Keine Admins."
        text += "\n\nHinzufügen: `/addadmin USER_ID Name`"
        await query.edit_message_text(text, parse_mode="Markdown")
        return

    if data == "adm_add_history":
        await query.edit_message_text(
            "📥 `/addhistory 2024-03-15 20 1:Dominik 2:Alex 3:Max`\n\nFormat: Datum BuyIn 1:Name 2:Name ...",
            parse_mode="Markdown"
        )
        return

    if data == "adm_del_tournament":
        await query.edit_message_text(
            "🗑 `/deletetournament` — zeigt Liste\n`/deletetournament ID` — löscht Turnier",
            parse_mode="Markdown"
        )
        return

    if data == "adm_export_db":
        if not os.path.exists(DB_FILE):
            await query.edit_message_text("❌ Keine Datenbank gefunden.")
            return
        await query.edit_message_text("💾 Datenbank wird gesendet...")
        with open(DB_FILE, "rb") as f:
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=f,
                filename=f"poker_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.db",
                caption="💾 Datenbank-Backup"
            )
        return

    if data == "adm_import_db":
        db_set("waiting_for_db_import", str(query.from_user.id))
        await query.edit_message_text(
            "📤 *DB importieren*\n\nSende jetzt die `.db` Datei als Anhang.\n\n"
            "⚠️ Aktuelle DB wird überschrieben (Backup wird erstellt).",
            parse_mode="Markdown"
        )
        return


# ─────────────────────────────────────────────
#  SEATDRAW
# ─────────────────────────────────────────────
async def seatdraw_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Randomly assign seats to all active players."""
    import random
    active = json.loads(db_get("active_players") or "[]")
    if not active:
        await update.message.reply_text("❌ Keine Spieler. /addplayer benutzen.")
        return
    shuffled = active[:]
    random.shuffle(shuffled)
    seat_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

    # Animated reveal — send "drawing..." first then edit
    msg = await update.message.reply_text("🎲 *Ziehe Sitzplätze...*", parse_mode="Markdown")
    await asyncio.sleep(1.5)

    lines = [f"{seat_emojis[i] if i < len(seat_emojis) else str(i+1)+'.'} {name}"
             for i, name in enumerate(shuffled)]
    await msg.edit_text(
        f"🎰 *Sitzplatz-Ziehung*\n\n" + "\n".join(lines) + "\n\n_Viel Glück allen!_ 🃏",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────
#  HISTORY
# ─────────────────────────────────────────────
async def history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show tournament archive."""
    # Optional: /history 10 for last 10
    limit = 10
    if context.args:
        try:
            limit = int(context.args[0])
        except ValueError:
            pass
    limit = min(limit, 30)

    conn = sqlite3.connect(DB_FILE)
    c    = conn.cursor()
    c.execute("""SELECT t.id, t.date, t.num_players, t.buyin_amount, t.total_pot, r.player_name
                 FROM tournaments t
                 LEFT JOIN results r ON r.tournament_id = t.id AND r.place = 1
                 WHERE t.status = 'finished'
                 ORDER BY t.date DESC LIMIT ?""", (limit,))
    rows = c.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📋 Noch keine abgeschlossenen Turniere.")
        return

    lines = []
    for t_id, date, num_p, buyin, pot, winner in rows:
        date_str = date[:10]
        w = f"👑 {winner}" if winner else "?"
        lines.append(f"#{t_id} | {date_str} | {num_p}P | {buyin:.0f}€ BI | {pot:.0f}€ Pot | {w}")

    await update.message.reply_text(
        f"📋 *Turnier-Archiv (letzte {len(rows)}):*\n\n`" + "\n".join(lines) + "`\n\n"
        "_Details: `/history 20` für mehr_",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────
#  AUTO BACKUP  (daily at midnight)
# ─────────────────────────────────────────────
async def auto_backup(context: ContextTypes.DEFAULT_TYPE):
    """Send DB backup to all superadmins daily."""
    if not os.path.exists(DB_FILE):
        return
    for uid in SUPERADMIN_IDS:
        try:
            with open(DB_FILE, "rb") as f:
                await context.bot.send_document(
                    chat_id=uid,
                    document=f,
                    filename=f"poker_backup_{datetime.now().strftime('%Y%m%d')}.db",
                    caption=f"💾 Auto-Backup — {datetime.now().strftime('%d.%m.%Y %H:%M')}"
                )
        except Exception as e:
            logging.warning(f"Auto-backup to {uid} failed: {e}")


# ─────────────────────────────────────────────
#  SAVE CHAT ID
# ─────────────────────────────────────────────
async def save_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if db_get("main_chat_id") != chat_id:
        db_set("main_chat_id", chat_id)


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ BOT_TOKEN fehlt! Railway → Variables → BOT_TOKEN=...")
        return
    if not SUPERADMIN_IDS:
        print("⚠️  SUPERADMIN_IDS nicht gesetzt!")
        print("   Railway → Variables → SUPERADMIN_IDS=deine_telegram_id")
        print("   Deine ID: schreib @userinfobot auf Telegram")

    logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # Tournament
    app.add_handler(CommandHandler("start",           start))
    app.add_handler(CommandHandler("help",            help_cmd))
    app.add_handler(CommandHandler("newgame",         new_game))
    app.add_handler(CommandHandler("addplayer",       add_player_cmd))
    app.add_handler(CommandHandler("removeplayer",    remove_player))
    app.add_handler(CommandHandler("players",         list_players))
    app.add_handler(CommandHandler("chipset",         chipset_cmd))
    app.add_handler(CommandHandler("setchips",        set_chips))
    app.add_handler(CommandHandler("calculate",       calculate))
    app.add_handler(CommandHandler("payout",          payout_cmd))
    app.add_handler(CommandHandler("bustout",         bustout_cmd))
    app.add_handler(CommandHandler("endtournament",   end_tournament))
    # Stats
    app.add_handler(CommandHandler("stats",           stats_cmd))
    app.add_handler(CommandHandler("playerstats",     player_stats))
    app.add_handler(CommandHandler("status",          status_cmd))
    # Blind clock
    app.add_handler(CommandHandler("shotclock",       shotclock_cmd))
    app.add_handler(CommandHandler("nextlevel",       next_level_cmd))
    app.add_handler(CommandHandler("stopblind",       stop_blind_cmd))
    app.add_handler(CommandHandler("blinds",          blinds_cmd))
    app.add_handler(CommandHandler("blindstructure",  blind_structure_cmd))
    # Admin
    app.add_handler(CommandHandler("adminpanel",      admin_panel))
    app.add_handler(CommandHandler("addadmin",        add_admin_cmd))
    app.add_handler(CommandHandler("removeadmin",     remove_admin_cmd))
    app.add_handler(CommandHandler("addhistory",      admin_add_history))
    app.add_handler(CommandHandler("deletetournament",admin_delete_tournament))
    app.add_handler(CommandHandler("resetstats",      admin_reset_stats))
    app.add_handler(CommandHandler("exportdb",        export_db_cmd))
    app.add_handler(CommandHandler("importdb",        import_db_cmd))
    # Info
    app.add_handler(CommandHandler("changelog",       changelog_cmd))
    app.add_handler(CommandHandler("seatdraw",        seatdraw_cmd))
    app.add_handler(CommandHandler("history",         history_cmd))

    # Callbacks
    app.add_handler(CallbackQueryHandler(button_callback))

    # Documents (for DB import) + chat ID saving
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.ALL & ~filters.Document.ALL, save_chat_id))

    # Background jobs
    app.job_queue.run_repeating(check_blind_timer, interval=30, first=10)

    # Auto-backup daily at midnight
    now = datetime.now()
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    seconds_until_midnight = (midnight - now).total_seconds()
    app.job_queue.run_repeating(auto_backup, interval=86400, first=seconds_until_midnight)

    print("🃏 CDVPoker Bot v5.0 gestartet!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
