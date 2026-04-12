"""
🃏 CDVPoker Telegram Bot — v3.0
================================
Requirements:
    pip install "python-telegram-bot[job-queue]==21.9" matplotlib

Setup:
    BOT_TOKEN als Umgebungsvariable setzen (Railway → Variables)
    SUPERADMIN_IDS = Telegram User-IDs der Superadmins (kommagetrennt)
"""

import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8385498157:AAF9Kh7Y2XbS4kixWsviFZHQEh9uGspF6aI")
DB_FILE   = "poker_tournament.db"

# Superadmin Telegram User-IDs (kommagetrennt als Env-Variable)
# In Railway setzen: SUPERADMIN_IDS=123456789,987654321
# Deine Telegram-ID findest du mit @userinfobot
_raw_ids = os.environ.get("SUPERADMIN_IDS", "494730002")
SUPERADMIN_IDS: set[int] = {int(x.strip()) for x in _raw_ids.split(",") if x.strip().isdigit()}

# ─────────────────────────────────────────────
#  VORDEFINIERTE CHIP-SETS  ← hier anpassen!
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
#  VORDEFINIERTE SPIELER-LISTE  ← hier anpassen!
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
#  PAYOUT STRUCTURES
# ─────────────────────────────────────────────
def get_payout_structure(num_players: int) -> list[dict]:
    if num_players <= 5:
        return [{"place": 1, "percent": 65}, {"place": 2, "percent": 35}]
    elif num_players <= 8:
        return [{"place": 1, "percent": 50}, {"place": 2, "percent": 30}, {"place": 3, "percent": 20}]
    elif num_players <= 10:
        # 4th gets buy-in back
        return [
            {"place": 1, "percent": 45}, {"place": 2, "percent": 27},
            {"place": 3, "percent": 18}, {"place": 4, "percent": 10},
        ]
    else:
        return [
            {"place": 1, "percent": 40}, {"place": 2, "percent": 25},
            {"place": 3, "percent": 17}, {"place": 4, "percent": 11}, {"place": 5, "percent": 7},
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
        total_buyins REAL DEFAULT 0
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS tournaments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT DEFAULT (datetime('now')),
        num_players INTEGER,
        buyin_amount REAL,
        total_pot REAL,
        chip_config TEXT,
        chips_per_player INTEGER,
        status TEXT DEFAULT 'setup',
        notes TEXT DEFAULT ''
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER,
        player_name TEXT,
        place INTEGER,
        payout REAL,
        FOREIGN KEY (tournament_id) REFERENCES tournaments(id)
    )""")
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


def clear_session():
    for key in ["active_players", "buyin_amount", "chip_config",
                "current_blind_level", "blind_start_time", "blind_running",
                "busted_players", "blind_levels"]:
        db_del(key)


# ─────────────────────────────────────────────
#  PERMISSION HELPERS
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
        await update.message.reply_text("⛔ Nur Superadmins können diesen Befehl nutzen.")
        return False
    return True


# ─────────────────────────────────────────────
#  CHIP CALCULATOR
# ─────────────────────────────────────────────
def calculate_chip_distribution(chip_config: dict, num_players: int) -> dict:
    chips_per_player = {}
    value_per_player = 0
    for denom, count in chip_config.items():
        per_player = count // num_players
        chips_per_player[denom] = per_player
        value_per_player += int(denom) * per_player
    total_value = sum(int(d) * c for d, c in chip_config.items())
    return {"chips_per_player": chips_per_player, "value_per_player": value_per_player, "total_value": total_value}


# ─────────────────────────────────────────────
#  /start  /help
# ─────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    admin_mark = " 👑" if is_superadmin(uid) else (" 🔑" if is_admin(uid) else "")
    text = (
        f"🃏 *CDVPoker Bot{admin_mark}*\n\n"
        "🎮 *Turnier*\n"
        "`/newgame 6 20 3h` — Turnier starten\n"
        "`/addplayer` — Spieler wählen (Button-Menü)\n"
        "`/chipset` — Chip-Set wählen\n"
        "`/calculate` — Chip-Verteilung\n"
        "`/payout` — Payouts anzeigen\n"
        "`/bustout` — Spieler ausscheiden (Buttons)\n"
        "`/endtournament` — Turnier beenden & auswerten\n\n"
        "⏱ *Shot Clock*\n"
        "`/shotclock` — Timer starten\n"
        "`/blinds` — Aktuelle Blinds & Zeit\n"
        "`/blindstructure` — Alle Level\n"
        "`/nextlevel` — Nächstes Level\n"
        "`/stopblind` — Timer stoppen\n\n"
        "📊 *Statistik*\n"
        "`/stats` — Leaderboard\n"
        "`/playerstats [Name]` — Spieler-Stats + Grafik\n"
        "`/status` — Turnierstatus\n\n"
        "🔑 *Admin*\n"
        "`/adminpanel` — Admin-Funktionen\n"
        "`/addadmin ID Name` — Admin hinzufügen\n"
        "`/addhistory` — Altes Turnier eintragen\n"
        "`/deletetournament` — Turnier löschen\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


# ─────────────────────────────────────────────
#  ADMIN PANEL
# ─────────────────────────────────────────────
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return
    keyboard = [
        [InlineKeyboardButton("👥 Admins verwalten", callback_data="adm_list_admins")],
        [InlineKeyboardButton("🗑 Turnier löschen", callback_data="adm_del_tournament"),
         InlineKeyboardButton("📥 Altes Turnier eintragen", callback_data="adm_add_history")],
        [InlineKeyboardButton("🔄 Statistik zurücksetzen", callback_data="adm_reset_stats")],
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
        uid = int(context.args[0])
        uname = context.args[1]
    except ValueError:
        await update.message.reply_text("❌ Ungültige User-ID.")
        return
    conn = sqlite3.connect(DB_FILE)
    conn.execute("INSERT OR REPLACE INTO admins (user_id, username, role) VALUES (?,?,?)", (uid, uname, "admin"))
    conn.commit()
    conn.close()
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
        await update.message.reply_text("❌ Ungültige User-ID.")
        return
    conn = sqlite3.connect(DB_FILE)
    conn.execute("DELETE FROM admins WHERE user_id=?", (uid,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Admin {uid} entfernt.")


# ─────────────────────────────────────────────
#  NEW GAME
# ─────────────────────────────────────────────
async def new_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return
    clear_session()
    if len(context.args) >= 3:
        try:
            num_players = int(context.args[0])
            buyin = float(context.args[1])
            dur_str = context.args[2].lower().replace("h", "").replace("std", "")
            total_minutes = int(float(dur_str) * 60)
            await _setup_tournament(update.message.reply_text, num_players, buyin, total_minutes)
            return
        except (ValueError, IndexError):
            pass
    await update.message.reply_text(
        "🎰 *Neues Turnier*\n\n"
        "`/newgame [Spieler] [Buy-In €] [Dauer]`\n\n"
        "Beispiele:\n"
        "`/newgame 6 20 3h`\n`/newgame 8 15 2.5h`\n`/newgame 10 25 4h`",
        parse_mode="Markdown"
    )


async def _setup_tournament(reply_fn, num_players: int, buyin: float, total_minutes: int):
    levels = build_blind_levels(total_minutes)
    db_set("blind_levels", json.dumps(levels))
    db_set("buyin_amount", str(buyin))
    total_pot = buyin * num_players
    structure = get_payout_structure(num_players)
    place_map = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    payout_lines = [
        f"{place_map[e['place']-1]} Platz {e['place']}: {e['percent']}% = *{total_pot * e['percent'] / 100:.2f}€*"
        for e in structure
    ]
    blind_preview = [
        f"  Lvl {l['level']}: {l['small']:,}/{l['big']:,} — {l['minutes']}min" + (" 🔒" if l['level'] <= 3 else "")
        for l in levels[:4]
    ]
    blind_preview.append(f"  ... ({len(levels)} Level, {sum(l['minutes'] for l in levels)} min gesamt)")
    await reply_fn(
        f"🎰 *Turnier eingerichtet!*\n\n"
        f"👥 {num_players} Spieler  |  💶 {buyin:.0f}€ Buy-In  |  🏆 {total_pot:.0f}€ Pot\n"
        f"⏱ {total_minutes/60:.1f}h geplant\n\n"
        f"💰 *Payouts:*\n" + "\n".join(payout_lines) + "\n\n"
        f"🎯 *Blind-Vorschau:*\n" + "\n".join(blind_preview) + "\n\n"
        f"📋 *Nächste Schritte:*\n"
        f"1️⃣ /addplayer — Spieler wählen\n"
        f"2️⃣ /chipset — Chip-Set wählen\n"
        f"3️⃣ /shotclock — Timer starten\n"
        f"4️⃣ /bustout — Bustouts eintragen",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────
#  ADD PLAYER — Button-Menü
# ─────────────────────────────────────────────
def _build_player_keyboard(active: list) -> list:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT name FROM players ORDER BY name")
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


async def add_player_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        name = " ".join(context.args).strip()
        _toggle_player(name)
        active = json.loads(db_get("active_players") or "[]")
        action = "hinzugefügt" if name in active else "entfernt"
        await update.message.reply_text(
            f"✅ *{name}* {action}!\n👥 Spieler: {', '.join(active)} ({len(active)})",
            parse_mode="Markdown"
        )
    else:
        active = json.loads(db_get("active_players") or "[]")
        active_display = ", ".join(active) if active else "noch niemand"
        await update.message.reply_text(
            f"👥 *Spieler auswählen*\n\nAktuell dabei: _{active_display}_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(_build_player_keyboard(active))
        )


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


async def remove_player(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Verwendung: `/removeplayer Name`", parse_mode="Markdown")
        return
    name = " ".join(context.args).strip()
    active = json.loads(db_get("active_players") or "[]")
    if name not in active:
        await update.message.reply_text(f"❌ {name} nicht in der aktuellen Runde.")
        return
    active.remove(name)
    db_set("active_players", json.dumps(active))
    await update.message.reply_text(f"✅ {name} entfernt. Noch {len(active)} Spieler.")


async def list_players(update: Update, context: ContextTypes.DEFAULT_TYPE):
    active = json.loads(db_get("active_players") or "[]")
    if not active:
        await update.message.reply_text("👥 Keine Spieler. /addplayer benutzen.")
        return
    lines = [f"  {i+1}. {p}" for i, p in enumerate(active)]
    await update.message.reply_text(f"👥 *Spieler ({len(active)}):*\n\n" + "\n".join(lines), parse_mode="Markdown")


# ─────────────────────────────────────────────
#  CHIPSET
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


async def _show_chip_summary(reply_fn, chip_config: dict):
    active = json.loads(db_get("active_players") or "[]")
    num = len(active)
    total = sum(int(d) * c for d, c in chip_config.items())
    lines = [f"  • {d}er: {c} Stück" for d, c in sorted(chip_config.items(), key=lambda x: int(x[0]))]
    text = "✅ *Chip-Set gespeichert!*\n\n" + "\n".join(lines) + f"\n\nGesamtwert: *{total:,}*"
    if num > 0:
        dist = calculate_chip_distribution(chip_config, num)
        dist_lines = [f"  • {d}er: {c} Stück" for d, c in sorted(dist["chips_per_player"].items(), key=lambda x: int(x[0]))]
        text += f"\n\n🎯 *Pro Spieler ({num}):*\n" + "\n".join(dist_lines) + f"\n💰 Startwert: *{dist['value_per_player']:,}*"
    await reply_fn(text, parse_mode="Markdown")


async def calculate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chip_str = db_get("chip_config")
    if not chip_str:
        await update.message.reply_text("❌ Erst /chipset wählen.")
        return
    await _show_chip_summary(update.message.reply_text, json.loads(chip_str))


# ─────────────────────────────────────────────
#  PAYOUT
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
    total_pot = buyin * num
    structure = get_payout_structure(num)
    place_map = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    lines = [f"{place_map[e['place']-1]} Platz {e['place']}: {e['percent']}% = *{total_pot * e['percent'] / 100:.2f}€*"
             for e in structure]
    await update.message.reply_text(
        f"💰 *Payouts — {num} Spieler | {buyin:.0f}€ BI | {total_pot:.0f}€ Pot*\n\n" + "\n".join(lines),
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
    keyboard = []
    for name in remaining:
        keyboard.append([InlineKeyboardButton(f"💀 {name} ausscheiden", callback_data=f"bust_{name}")])
    busted_lines = ""
    if busted:
        lines = []
        for b in sorted(busted, key=lambda x: x["place"]):
            e = place_map.get(b["place"], f"#{b['place']}")
            lines.append(f"{e} {b['name']}")
        busted_lines = "\n\nAusgeschieden:\n" + "\n".join(lines)
    keyboard.append([InlineKeyboardButton("🏁 Turnier beenden & auswerten", callback_data="finish_tournament")])
    return busted_lines, keyboard


async def _send_bustout_menu(reply_fn, edit_fn=None):
    players = json.loads(db_get("active_players") or "[]")
    busted = json.loads(db_get("busted_players") or "[]")
    busted_names = [b["name"] for b in busted]
    remaining = [p for p in players if p not in busted_names]

    if not remaining:
        text = "✅ Alle ausgeschieden! /endtournament aufrufen."
        fn = edit_fn or reply_fn
        await fn(text)
        return

    busted_lines, keyboard = _build_bustout_keyboard(remaining, busted)
    text = (
        f"💀 *Bustout-Menü*\n\n"
        f"Noch im Spiel: *{len(remaining)}* von {len(players)}"
        + busted_lines
        + "\n\nWer scheidet aus?"
    )
    fn = edit_fn or reply_fn
    await fn(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def _handle_bustout(query, name: str, context: ContextTypes.DEFAULT_TYPE):
    players = json.loads(db_get("active_players") or "[]")
    busted = json.loads(db_get("busted_players") or "[]")
    busted_names = [b["name"] for b in busted]

    if name not in players:
        await query.answer(f"❌ {name} ist kein aktiver Spieler!")
        return
    if name in busted_names:
        await query.answer(f"⚠️ {name} ist bereits ausgeschieden!")
        return

    remaining = [p for p in players if p not in busted_names]
    place = len(remaining)
    busted.append({"name": name, "place": place})
    db_set("busted_players", json.dumps(busted))
    await query.answer(f"💀 {name} — Platz {place}")

    remaining_after = [p for p in players if p not in [b["name"] for b in busted]]

    if len(remaining_after) == 1:
        winner = remaining_after[0]
        busted.append({"name": winner, "place": 1})
        db_set("busted_players", json.dumps(busted))
        await query.edit_message_text(
            f"🏆 *{winner} GEWINNT!*\n\nLetzter Bustout: 💀 {name} (Platz {place})\n\n_/endtournament für die Auswertung_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏁 Auswertung starten", callback_data="finish_tournament")
            ]])
        )
        return

    await _send_bustout_menu(None, edit_fn=query.edit_message_text)


# ─────────────────────────────────────────────
#  END TOURNAMENT
# ─────────────────────────────────────────────
async def end_tournament(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return
    await _finalize_tournament(update.message.reply_text, context)


async def _finalize_tournament(reply_fn, context: ContextTypes.DEFAULT_TYPE):
    db_set("blind_running", "0")  # Stop timer immediately!

    players = json.loads(db_get("active_players") or "[]")
    busted = json.loads(db_get("busted_players") or "[]")
    buyin_str = db_get("buyin_amount")
    buyin = float(buyin_str) if buyin_str else 0.0

    if not players:
        await reply_fn("❌ Keine aktiven Spieler. Zuerst /newgame und /addplayer.")
        return

    if busted:
        ordered = sorted(busted, key=lambda x: x["place"])
        placements = [b["name"] for b in ordered]
        for p in players:
            if p not in placements:
                placements.append(p)
    else:
        placements = list(players)

    num_players = len(players)
    total_pot = buyin * num_players
    structure = get_payout_structure(num_players)
    payouts = {e["place"]: total_pot * e["percent"] / 100 for e in structure}

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO tournaments (num_players, buyin_amount, total_pot, chip_config, status) VALUES (?,?,?,?,?)",
              (num_players, buyin, total_pot, db_get("chip_config") or "{}", "finished"))
    tournament_id = c.lastrowid

    place_emoji = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    result_lines = []
    winner_name = placements[0]
    winner_payout = payouts.get(1, 0.0)

    for i, name in enumerate(placements):
        place = i + 1
        payout = payouts.get(place, 0.0)
        profit = payout - buyin
        c.execute("INSERT OR IGNORE INTO players (name) VALUES (?)", (name,))
        c.execute("INSERT INTO results (tournament_id, player_name, place, payout) VALUES (?,?,?,?)",
                  (tournament_id, name, place, payout))
        c.execute("""UPDATE players SET total_tournaments=total_tournaments+1,
            total_wins=total_wins+?, total_earnings=total_earnings+?, total_buyins=total_buyins+?
            WHERE name=?""", (1 if place == 1 else 0, payout, buyin, name))
        emoji = place_emoji[i] if i < len(place_emoji) else f"{place}."
        if payout > 0:
            result_lines.append(f"{emoji} *{name}* — {payout:.2f}€ ({'+'if profit>=0 else ''}{profit:.2f}€)")
        else:
            result_lines.append(f"{emoji} {name} — -{buyin:.2f}€")

    conn.commit()
    conn.close()

    winner_profit = winner_payout - buyin
    winner_roi = (winner_profit / buyin * 100) if buyin > 0 else 0

    await reply_fn(
        f"🏆 *TURNIER #{tournament_id} BEENDET!*\n\n"
        f"👑 *SIEGER: {winner_name}*\n"
        f"💰 Gewinn: *{winner_payout:.2f}€*  |  Buy-In: {buyin:.2f}€\n"
        f"📈 ROI: *+{winner_roi:.0f}%*  |  Profit: *+{winner_profit:.2f}€*\n\n"
        f"📋 *Ergebnis:*\n" + "\n".join(result_lines) +
        f"\n\n💵 Pot: {total_pot:.2f}€  |  {num_players} Spieler",
        parse_mode="Markdown"
    )

    chat_id = db_get("main_chat_id")
    if chat_id:
        chart_path = _generate_profit_chart(placements, payouts, buyin, tournament_id)
        if chart_path:
            with open(chart_path, "rb") as f:
                await context.bot.send_photo(chat_id=int(chat_id), photo=f,
                                             caption=f"📊 Turnier #{tournament_id} — Profit/Verlust")
            os.remove(chart_path)

    await _send_player_summaries(context, placements, payouts, buyin, tournament_id)
    clear_session()


# ─────────────────────────────────────────────
#  CHARTS
# ─────────────────────────────────────────────
def _generate_profit_chart(placements, payouts, buyin, tournament_id) -> Optional[str]:
    try:
        import matplotlib; matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    profits = [payouts.get(i + 1, 0.0) - buyin for i in range(len(placements))]
    colors = ["#2ecc71" if p >= 0 else "#e74c3c" for p in profits]
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
    for s in ["top", "right"]: ax.spines[s].set_visible(False)
    for s in ["left", "bottom"]: ax.spines[s].set_color("#444466")
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
    for s in ["top", "right"]: ax.spines[s].set_visible(False)
    for s in ["left", "bottom"]: ax.spines[s].set_color("#444466")
    plt.tight_layout()
    path = f"/tmp/poker_player_{name.replace(' ', '_')}.png"
    plt.savefig(path, dpi=150, facecolor=fig.get_facecolor()); plt.close()
    return path


async def _send_player_summaries(context, placements, payouts, buyin, tournament_id):
    chat_id = db_get("main_chat_id")
    if not chat_id:
        return
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    place_map = {1: "🥇", 2: "🥈", 3: "🥉"}
    for i, name in enumerate(placements):
        place = i + 1
        payout = payouts.get(place, 0.0)
        profit_this = payout - buyin
        c.execute("SELECT total_tournaments, total_wins, total_earnings, total_buyins FROM players WHERE name=?", (name,))
        row = c.fetchone()
        if not row:
            continue
        t, w, e, b = row
        total_profit = e - b
        roi = (total_profit / b * 100) if b > 0 else 0
        win_rate = (w / t * 100) if t > 0 else 0
        emoji = place_map.get(place, f"#{place}")
        profit_str = f"+{profit_this:.2f}€" if profit_this >= 0 else f"{profit_this:.2f}€"
        total_str = f"+{total_profit:.2f}€" if total_profit >= 0 else f"{total_profit:.2f}€"
        roi_str = f"+{roi:.0f}%" if roi >= 0 else f"{roi:.0f}%"
        await context.bot.send_message(chat_id=int(chat_id), parse_mode="Markdown", text=(
            f"{emoji} *{name}* — Turnier #{tournament_id}\n"
            f"├ Platz {place} von {len(placements)}\n"
            f"├ Payout: {payout:.2f}€  (Buy-In: {buyin:.2f}€)\n"
            f"└ Heute: *{profit_str}*\n\n"
            f"📊 *Gesamtbilanz:*\n"
            f"├ {t} Turniere  |  {w} Siege  ({win_rate:.0f}% WR)\n"
            f"├ Profit: *{total_str}*\n"
            f"└ ROI: *{roi_str}*"
        ))
        await asyncio.sleep(0.4)
    conn.close()


# ─────────────────────────────────────────────
#  STATS
# ─────────────────────────────────────────────
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""SELECT name, total_tournaments, total_wins, total_earnings, total_buyins
                 FROM players WHERE total_tournaments>0
                 ORDER BY (total_earnings-total_buyins) DESC LIMIT 15""")
    rows = c.fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("📊 Noch keine Daten.")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (name, t, w, e, b) in enumerate(rows):
        profit = e - b
        roi = (profit / b * 100) if b > 0 else 0
        medal = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{medal} *{name}*\n   🎮 {t}x  🏆 {w}x  💰 {'+'if profit>=0 else ''}{profit:.0f}€  📈 {'+'if roi>=0 else ''}{roi:.0f}%")
    await update.message.reply_text("📊 *Leaderboard*\n\n" + "\n\n".join(lines), parse_mode="Markdown")


async def player_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Verwendung: `/playerstats Dominik`", parse_mode="Markdown")
        return
    name = " ".join(context.args).strip()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT * FROM players WHERE name=?", (name,))
    player = c.fetchone()
    if not player:
        await update.message.reply_text(f"❌ '{name}' nicht gefunden.")
        conn.close()
        return
    _, pname, _, t, w, e, b = player
    profit = e - b
    roi = (profit / b * 100) if b > 0 else 0
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
        recent_lines.append(f"  {place_map.get(place, str(place)+'.') } {date[:10]} Platz {place}  {'+'if p>=0 else ''}{p:.0f}€")
    profit_str = f"+{profit:.2f}€" if profit >= 0 else f"{profit:.2f}€"
    text = (
        f"👤 *{pname}*\n\n"
        f"🎮 {t} Turniere  |  🏆 {w} Siege  ({win_rate:.0f}% WR)\n"
        f"💰 Profit: *{profit_str}*  |  ROI: *{'+'if roi>=0 else ''}{roi:.0f}%*\n"
        f"📊 Einnahmen: {e:.2f}€  |  Kosten: {b:.2f}€"
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
    level = get_active_blind_levels()[0]
    end_time = (datetime.now() + timedelta(minutes=level["minutes"])).strftime("%H:%M")
    keyboard = [[
        InlineKeyboardButton("⏭ Nächstes Level", callback_data="next_level"),
        InlineKeyboardButton("⏹ Stoppen", callback_data="stop_blind"),
    ], [
        InlineKeyboardButton("⏱ Zeit prüfen", callback_data="time_left"),
        InlineKeyboardButton("📋 Alle Level", callback_data="all_levels"),
    ]]
    await update.message.reply_text(
        f"⏱ *Shot Clock gestartet!*\n\n*Level {level['level']}*\n"
        f"🔵 Small: {level['small']:,}  🔴 Big: {level['big']:,}\n"
        f"⏰ {level['minutes']} min  |  Ende: {end_time}",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def next_level_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _advance_level(update.message.reply_text)


async def _advance_level(reply_fn):
    levels = get_active_blind_levels()
    current = int(db_get("current_blind_level") or "1")
    nxt = current + 1
    if nxt > len(levels):
        await reply_fn("🏁 Letztes Blind-Level erreicht!")
        return
    db_set("current_blind_level", str(nxt))
    db_set("blind_start_time", datetime.now().isoformat())
    level = levels[nxt - 1]; prev = levels[current - 1]
    end_time = (datetime.now() + timedelta(minutes=level["minutes"])).strftime("%H:%M")
    keyboard = [[
        InlineKeyboardButton("⏭ Nächstes Level", callback_data="next_level"),
        InlineKeyboardButton("⏱ Zeit", callback_data="time_left"),
    ]]
    await reply_fn(
        f"⏭ *Level {nxt} — Blinds erhöht!*\n\n"
        f"War: {prev['small']:,}/{prev['big']:,}\n"
        f"Jetzt: *{level['small']:,}/{level['big']:,}*\n"
        f"⏰ {level['minutes']} min  |  Ende: {end_time}",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def stop_blind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_set("blind_running", "0")
    await update.message.reply_text("⏹ Blind-Timer gestoppt.")


async def blinds_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if db_get("blind_running") != "1":
        await update.message.reply_text("⏹ Kein aktiver Timer. /shotclock zum Starten.")
        return
    levels = get_active_blind_levels()
    current = int(db_get("current_blind_level") or "1")
    level = levels[current - 1]
    elapsed = datetime.now() - datetime.fromisoformat(db_get("blind_start_time"))
    remaining = max(timedelta(minutes=level["minutes"]) - elapsed, timedelta(0))
    mins, secs = divmod(int(remaining.total_seconds()), 60)
    next_info = f"\n_Nächstes Level: {levels[current]['small']:,}/{levels[current]['big']:,}_" if current < len(levels) else ""
    keyboard = [[
        InlineKeyboardButton("⏭ Nächstes Level", callback_data="next_level"),
        InlineKeyboardButton("🔄 Aktualisieren", callback_data="time_left"),
    ]]
    await update.message.reply_text(
        f"⏱ *Level {current}*\n\n🔵 {level['small']:,}  🔴 {level['big']:,}\n⏰ Verbleibend: *{mins}:{secs:02d}*" + next_info,
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def blind_structure_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    levels = get_active_blind_levels()
    current = int(db_get("current_blind_level") or "0")
    lines = [f"Lvl {l['level']:2d} | {l['small']:>5,}/{l['big']:>6,} | {l['minutes']:2d}min" + (" ◀️" if l['level'] == current else "") for l in levels]
    await update.message.reply_text("📋 *Blind-Struktur:*\n\n`" + "\n".join(lines) + "`", parse_mode="Markdown")


# ─────────────────────────────────────────────
#  STATUS
# ─────────────────────────────────────────────
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    active = json.loads(db_get("active_players") or "[]")
    busted = json.loads(db_get("busted_players") or "[]")
    remaining = [p for p in active if p not in [b["name"] for b in busted]]
    buyin_str = db_get("buyin_amount")
    current_blind = db_get("current_blind_level")
    blind_running = db_get("blind_running")
    text = "📊 *Turnierstatus*\n\n"
    text += f"👥 Spieler: {len(active)} ({', '.join(active) if active else 'keine'})\n"
    text += f"🃏 Noch im Spiel: {len(remaining)} ({', '.join(remaining) if remaining else '—'})\n"
    if buyin_str:
        b = float(buyin_str)
        text += f"💶 Buy-In: {b:.0f}€  |  Pot: {b*len(active):.0f}€\n"
    if blind_running == "1" and current_blind:
        lvl = get_active_blind_levels()[int(current_blind) - 1]
        text += f"⏱ Level {current_blind}: {lvl['small']:,}/{lvl['big']:,} (läuft)\n"
    else:
        text += "⏱ Blind-Timer: gestoppt\n"
    await update.message.reply_text(text, parse_mode="Markdown")


# ─────────────────────────────────────────────
#  ADMIN HISTORY & DELETE
# ─────────────────────────────────────────────
async def admin_add_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update):
        return
    if len(context.args) < 4:
        await update.message.reply_text(
            "📥 *Altes Turnier eintragen:*\n\n"
            "`/addhistory DATUM BUYIN 1:Name 2:Name 3:Name ...`\n\n"
            "Beispiel:\n`/addhistory 2024-03-15 20 1:Dominik 2:Alex 3:Max 4:Jonas`",
            parse_mode="Markdown"
        )
        return
    try:
        date_str = context.args[0]
        buyin = float(context.args[1])
        placements_raw = context.args[2:]
        placements: dict[int, str] = {}
        for item in placements_raw:
            place_str, name = item.split(":", 1)
            placements[int(place_str)] = name
        num_players = len(placements)
        total_pot = buyin * num_players
        structure = get_payout_structure(num_players)
        payouts = {e["place"]: total_pot * e["percent"] / 100 for e in structure}
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("INSERT INTO tournaments (date, num_players, buyin_amount, total_pot, chip_config, status) VALUES (?,?,?,?,?,?)",
                  (date_str + " 20:00:00", num_players, buyin, total_pot, "{}", "finished"))
        tournament_id = c.lastrowid
        place_emoji = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]
        lines = []
        for place in sorted(placements.keys()):
            name = placements[place]
            payout = payouts.get(place, 0.0)
            c.execute("INSERT OR IGNORE INTO players (name) VALUES (?)", (name,))
            c.execute("INSERT INTO results (tournament_id, player_name, place, payout) VALUES (?,?,?,?)",
                      (tournament_id, name, place, payout))
            c.execute("""UPDATE players SET total_tournaments=total_tournaments+1, total_wins=total_wins+?,
                total_earnings=total_earnings+?, total_buyins=total_buyins+? WHERE name=?""",
                      (1 if place == 1 else 0, payout, buyin, name))
            emoji = place_emoji[place - 1] if place <= len(place_emoji) else f"{place}."
            profit = payout - buyin
            lines.append(f"{emoji} {name} — {payout:.2f}€ ({'+'if profit>=0 else ''}{profit:.2f}€)")
        conn.commit()
        conn.close()
        await update.message.reply_text(
            f"✅ *Turnier #{tournament_id} eingetragen ({date_str})*\n\n" + "\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Fehler: {e}")


async def admin_delete_tournament(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_superadmin(update):
        return
    if not context.args:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT id, date, num_players, buyin_amount FROM tournaments ORDER BY id DESC LIMIT 10")
        rows = c.fetchall()
        conn.close()
        if not rows:
            await update.message.reply_text("Keine Turniere vorhanden.")
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
    c = conn.cursor()
    c.execute("SELECT buyin_amount FROM tournaments WHERE id=?", (t_id,))
    row = c.fetchone()
    if not row:
        await update.message.reply_text(f"❌ Turnier #{t_id} nicht gefunden.")
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
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Turnier #{t_id} gelöscht. Statistiken korrigiert.")


async def admin_reset_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_superadmin(update):
        return
    keyboard = [[
        InlineKeyboardButton("⚠️ JA, alles löschen", callback_data="confirm_reset_stats"),
        InlineKeyboardButton("❌ Abbrechen", callback_data="cancel_reset"),
    ]]
    await update.message.reply_text(
        "⚠️ *Alle Statistiken löschen?*\nKann nicht rückgängig gemacht werden!",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


# ─────────────────────────────────────────────
#  BLIND BACKGROUND JOB
# ─────────────────────────────────────────────
async def check_blind_timer(context: ContextTypes.DEFAULT_TYPE):
    if db_get("blind_running") != "1":
        return
    current_str = db_get("current_blind_level")
    start_str = db_get("blind_start_time")
    if not current_str or not start_str:
        return
    levels = get_active_blind_levels()
    current = int(current_str)
    if current > len(levels):
        return
    level = levels[current - 1]
    elapsed = datetime.now() - datetime.fromisoformat(start_str)
    level_dur = timedelta(minutes=level["minutes"])

    warned = db_get(f"warned_{current}")
    if elapsed >= level_dur - timedelta(minutes=2) and not warned:
        db_set(f"warned_{current}", "1")
        chat_id = db_get("main_chat_id")
        if chat_id:
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=f"⚠️ *Noch 2 Minuten — Level {current}!*\n{level['small']:,}/{level['big']:,}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏭ Level wechseln", callback_data="next_level")]])
            )

    if elapsed >= level_dur:
        nxt = current + 1
        if nxt <= len(levels):
            db_set("current_blind_level", str(nxt))
            db_set("blind_start_time", datetime.now().isoformat())
            db_del(f"warned_{current}")
            next_level = levels[nxt - 1]
            chat_id = db_get("main_chat_id")
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
    data = query.data

    if data.startswith("bust_"):
        await _handle_bustout(query, data[5:], context)
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
        levels = get_active_blind_levels()
        current = int(db_get("current_blind_level") or "1")
        level = levels[current - 1]
        elapsed = datetime.now() - datetime.fromisoformat(db_get("blind_start_time") or datetime.now().isoformat())
        remaining = max(timedelta(minutes=level["minutes"]) - elapsed, timedelta(0))
        mins, secs = divmod(int(remaining.total_seconds()), 60)
        keyboard = [[
            InlineKeyboardButton("⏭ Nächstes Level", callback_data="next_level"),
            InlineKeyboardButton("🔄 Aktualisieren", callback_data="time_left"),
        ]]
        await query.edit_message_text(
            f"⏱ *Level {current}*\n\n🔵 {level['small']:,}  🔴 {level['big']:,}\n⏰ Verbleibend: *{mins}:{secs:02d}*",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if data == "all_levels":
        levels = get_active_blind_levels()
        current = int(db_get("current_blind_level") or "0")
        lines = [f"Lvl {l['level']:2d} | {l['small']:>5,}/{l['big']:>6,} | {l['minutes']:2d}min" + (" ◀️" if l['level'] == current else "") for l in levels]
        await query.edit_message_text("📋 *Blind-Struktur:*\n\n`" + "\n".join(lines) + "`", parse_mode="Markdown")
        return

    # ── Player Menu ──
    if data.startswith("ap_"):
        action = data[3:]
        if action == "done":
            active = json.loads(db_get("active_players") or "[]")
            await query.edit_message_text(
                f"✅ *{len(active)} Spieler:* {', '.join(active)}\n\nNächster Schritt: /chipset",
                parse_mode="Markdown")
            return
        if action == "custom":
            await query.edit_message_text("✏️ Neuen Spieler: `/addplayer Name`", parse_mode="Markdown")
            return
        _toggle_player(action)
        active = json.loads(db_get("active_players") or "[]")
        active_display = ", ".join(active) if active else "noch niemand"
        await query.edit_message_text(
            f"👥 *Spieler auswählen*\n\nAktuell dabei: _{active_display}_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(_build_player_keyboard(active))
        )
        return

    # ── Chipset ──
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

    # ── Admin Callbacks ──
    if data == "adm_clear_session":
        clear_session()
        await query.edit_message_text("🧹 Session geleert. /newgame für neues Turnier.")
        return

    if data == "adm_reset_stats":
        keyboard = [[
            InlineKeyboardButton("⚠️ JA, alles löschen", callback_data="confirm_reset_stats"),
            InlineKeyboardButton("❌ Abbrechen", callback_data="cancel_reset"),
        ]]
        await query.edit_message_text("⚠️ *Alle Statistiken löschen?*", parse_mode="Markdown",
                                      reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "confirm_reset_stats":
        if not is_superadmin(query.from_user.id):
            await query.edit_message_text("⛔ Nur Superadmins.")
            return
        conn = sqlite3.connect(DB_FILE)
        conn.execute("DELETE FROM results")
        conn.execute("DELETE FROM tournaments")
        conn.execute("UPDATE players SET total_tournaments=0, total_wins=0, total_earnings=0, total_buyins=0")
        conn.commit()
        conn.close()
        await query.edit_message_text("✅ Alle Statistiken zurückgesetzt.")
        return

    if data == "cancel_reset":
        await query.edit_message_text("❌ Abgebrochen.")
        return

    if data == "adm_list_admins":
        supers = [f"👑 Superadmin (ENV): {uid}" for uid in SUPERADMIN_IDS]
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT user_id, username, role FROM admins")
        admins_db = [f"🔑 {un} ({uid})" for uid, un, _ in c.fetchall()]
        conn.close()
        text = "*Admins:*\n\n" + "\n".join(supers + admins_db) if (supers or admins_db) else "Keine Admins."
        text += "\n\nHinzufügen: `/addadmin USER_ID Name`"
        await query.edit_message_text(text, parse_mode="Markdown")
        return

    if data == "adm_add_history":
        await query.edit_message_text(
            "📥 `/addhistory 2024-03-15 20 1:Dominik 2:Alex 3:Max`", parse_mode="Markdown")
        return

    if data == "adm_del_tournament":
        await query.edit_message_text(
            "🗑 `/deletetournament` — zeigt Liste\n`/deletetournament ID` — löscht Turnier",
            parse_mode="Markdown")
        return

    if data == "adm_edit_tournament":
        await query.edit_message_text(
            "✏️ Löschen + neu eintragen:\n1. `/deletetournament ID`\n2. `/addhistory ...`",
            parse_mode="Markdown")
        return


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
        print("⚠️  SUPERADMIN_IDS nicht gesetzt. Railway → Variables → SUPERADMIN_IDS=deine_telegram_id")
        print("   Deine Telegram-ID findest du mit @userinfobot")

    logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("newgame", new_game))
    app.add_handler(CommandHandler("addplayer", add_player_cmd))
    app.add_handler(CommandHandler("removeplayer", remove_player))
    app.add_handler(CommandHandler("players", list_players))
    app.add_handler(CommandHandler("chipset", chipset_cmd))
    app.add_handler(CommandHandler("setchips", set_chips))
    app.add_handler(CommandHandler("calculate", calculate))
    app.add_handler(CommandHandler("payout", payout_cmd))
    app.add_handler(CommandHandler("bustout", bustout_cmd))
    app.add_handler(CommandHandler("endtournament", end_tournament))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("playerstats", player_stats))
    app.add_handler(CommandHandler("shotclock", shotclock_cmd))
    app.add_handler(CommandHandler("nextlevel", next_level_cmd))
    app.add_handler(CommandHandler("stopblind", stop_blind_cmd))
    app.add_handler(CommandHandler("blinds", blinds_cmd))
    app.add_handler(CommandHandler("blindstructure", blind_structure_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("adminpanel", admin_panel))
    app.add_handler(CommandHandler("addadmin", add_admin_cmd))
    app.add_handler(CommandHandler("removeadmin", remove_admin_cmd))
    app.add_handler(CommandHandler("addhistory", admin_add_history))
    app.add_handler(CommandHandler("deletetournament", admin_delete_tournament))
    app.add_handler(CommandHandler("resetstats", admin_reset_stats))

    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.ALL, save_chat_id))

    app.job_queue.run_repeating(check_blind_timer, interval=30, first=10)

    print("🃏 CDVPoker Bot v3.0 gestartet!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
