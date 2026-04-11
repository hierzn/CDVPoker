"""
🃏 Poker Tournament Telegram Bot
=================================
Features:
- Chip distribution calculator
- Player management & statistics
- Automatic payout calculation
- Shot clock with blind structure

Requirements:
    pip install python-telegram-bot==20.7 matplotlib

Setup:
    1. Create a bot via @BotFather in Telegram → get your TOKEN
    2. Replace BOT_TOKEN below with your token
    3. Run: python poker_bot.py
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
    ContextTypes, ConversationHandler, MessageHandler, filters
)

# ─────────────────────────────────────────────
#  CONFIG — Replace with your token!
# ─────────────────────────────────────────────
BOT_TOKEN = "8385498157:AAF9Kh7Y2XbS4kixWsviFZHQEh9uGspF6aI"
DB_FILE = "poker_tournament.db"

# ─────────────────────────────────────────────
#  BLIND STRUCTURE — dynamisch berechnet
# ─────────────────────────────────────────────

# Feste Blind-Stufen (werden mit berechneten Zeiten kombiniert)
BLIND_STEPS = [
    (25,   50),
    (50,   100),
    (75,   150),
    (100,  200),
    (150,  300),
    (200,  400),
    (300,  600),
    (400,  800),
    (500,  1000),
    (750,  1500),
    (1000, 2000),
    (1500, 3000),
    (2000, 4000),
    (3000, 6000),
    (5000, 10000),
]

def build_blind_levels(total_minutes: int) -> list[dict]:
    """
    Berechnet Blind-Level-Zeiten für eine gewünschte Turnierdauer.

    Strategie:
    - 12 Level werden verwendet (mehr wenn Turnier sehr lang)
    - Level 1-3 bekommen 40% mehr Zeit als der Durchschnitt (Spielspaß am Anfang)
    - Restliche Zeit gleichmäßig auf die verbleibenden Level verteilt
    - Mindestlevel-Zeit: 5 Minuten
    """
    # Anzahl Level abhängig von Turnierdauer
    if total_minutes <= 90:
        num_levels = 8
    elif total_minutes <= 150:
        num_levels = 10
    elif total_minutes <= 240:
        num_levels = 12
    else:
        num_levels = 14

    num_levels = min(num_levels, len(BLIND_STEPS))

    # Early levels (1-3) bekommen Bonus-Zeit (+40%)
    # Formel: 3 * 1.4 * x + (n-3) * x = total  →  x = total / (3*1.4 + n-3)
    early_factor = 1.4
    num_early = 3
    base_minutes = total_minutes / (num_early * early_factor + (num_levels - num_early))
    base_minutes = max(base_minutes, 5)  # Mindestens 5 Minuten pro Level

    levels = []
    for i in range(num_levels):
        level_num = i + 1
        small, big = BLIND_STEPS[i]

        if i < num_early:
            minutes = round(base_minutes * early_factor)
        else:
            minutes = round(base_minutes)

        minutes = max(minutes, 5)  # Sicherheitsnetz
        levels.append({
            "level": level_num,
            "minutes": minutes,
            "small": small,
            "big": big,
        })

    return levels


def get_active_blind_levels() -> list[dict]:
    """Lädt die aktuell gespeicherten Blind-Level oder gibt Default zurück."""
    saved = db_get("blind_levels")
    if saved:
        return json.loads(saved)
    # Fallback: 3 Stunden Standard
    return build_blind_levels(180)

# ─────────────────────────────────────────────
#  PAYOUT STRUCTURES
# ─────────────────────────────────────────────
def get_payout_structure(num_players: int) -> list[dict]:
    """Returns payout percentages based on player count."""
    if num_players <= 5:
        # Only top 2 get paid
        return [
            {"place": 1, "percent": 65},
            {"place": 2, "percent": 35},
        ]
    elif num_players == 6:
        # 3rd gets buy-in back
        return [
            {"place": 1, "percent": 50},
            {"place": 2, "percent": 30},
            {"place": 3, "percent": 20},  # roughly buy-in back
        ]
    elif num_players <= 9:
        return [
            {"place": 1, "percent": 50},
            {"place": 2, "percent": 30},
            {"place": 3, "percent": 20},
        ]
    else:
        # 10+ players: top 4 paid
        return [
            {"place": 1, "percent": 45},
            {"place": 2, "percent": 27},
            {"place": 3, "percent": 18},
            {"place": 4, "percent": 10},
        ]


# ─────────────────────────────────────────────
#  DATABASE SETUP
# ─────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Players table
    c.execute("""
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            added_at TEXT DEFAULT (datetime('now')),
            total_tournaments INTEGER DEFAULT 0,
            total_wins INTEGER DEFAULT 0,
            total_earnings REAL DEFAULT 0,
            total_buyins REAL DEFAULT 0
        )
    """)

    # Tournaments table
    c.execute("""
        CREATE TABLE IF NOT EXISTS tournaments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT DEFAULT (datetime('now')),
            num_players INTEGER,
            buyin_amount REAL,
            total_pot REAL,
            chip_config TEXT,
            chips_per_player INTEGER,
            status TEXT DEFAULT 'setup'
        )
    """)

    # Tournament results
    c.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tournament_id INTEGER,
            player_name TEXT,
            place INTEGER,
            payout REAL,
            FOREIGN KEY (tournament_id) REFERENCES tournaments(id)
        )
    """)

    # Bot state (persistent across restarts)
    c.execute("""
        CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    conn.commit()
    conn.close()


def db_get(key: str) -> Optional[str]:
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT value FROM bot_state WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def db_set(key: str, value: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def db_del(key: str):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM bot_state WHERE key = ?", (key,))
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────
#  CHIP CALCULATOR
# ─────────────────────────────────────────────
def calculate_chip_distribution(chip_config: dict, num_players: int) -> dict:
    """
    chip_config: {"25": 100, "100": 50, "500": 20, "1000": 10}
    Returns distribution per player and total chip value.
    """
    total_chips = {denom: count for denom, count in chip_config.items()}
    total_value = sum(int(d) * c for d, c in chip_config.items())
    chips_per_player = {}
    value_per_player = 0

    for denom, count in chip_config.items():
        per_player = count // num_players
        chips_per_player[denom] = per_player
        value_per_player += int(denom) * per_player

    return {
        "chips_per_player": chips_per_player,
        "value_per_player": value_per_player,
        "total_value": total_value,
        "total_chips": total_chips,
    }


# ─────────────────────────────────────────────
#  COMMAND HANDLERS
# ─────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🃏 *Willkommen beim Poker Turnier Bot!*\n\n"
        "Hier sind alle verfügbaren Befehle:\n\n"
        "🎮 *Turnier*\n"
        "/newgame 6 20 3h — Turnier starten (Spieler, Buy-In, Dauer)\n"
        "/setchips 25:100 100:50 — Chips konfigurieren\n"
        "/calculate — Chip-Verteilung berechnen\n"
        "/payout — Payout berechnen\n"
        "/bustout — Spieler ausscheiden (Button-Menü)\n"
        "/endtournament — Turnier beenden & Auswertung\n\n"
        "⏱ *Shot Clock*\n"
        "/shotclock — Shot Clock starten\n"
        "/nextlevel — Nächstes Blind-Level\n"
        "/stopblind — Blind-Timer stoppen\n"
        "/blinds — Aktuelle Blinds anzeigen\n"
        "/blindstructure — Alle Blind-Level anzeigen\n\n"
        "👥 *Spieler*\n"
        "/addplayer [Name] — Spieler hinzufügen\n"
        "/players — Alle Spieler anzeigen\n"
        "/stats — Gesamtstatistik\n"
        "/playerstats [Name] — Statistik eines Spielers\n\n"
        "ℹ️ *Sonstiges*\n"
        "/status — Aktueller Turnierstatus\n"
        "/help — Diese Hilfe"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


# ─── NEW GAME ───────────────────────────────
async def new_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Startet ein neues Turnier mit allen Parametern auf einmal.
    Verwendung: /newgame [Spieler] [Buyin] [Dauer]
    Beispiel:   /newgame 6 20 3h
    Beispiel:   /newgame 8 15 2.5h
    Auch möglich: /newgame  (dann wird Schritt für Schritt gefragt)
    """
    # Alles zurücksetzen
    for key in ["current_tournament", "active_players", "chip_config",
                "buyin_amount", "blind_levels", "current_blind_level",
                "blind_start_time", "blind_running"]:
        db_del(key)

    # Versuche Parameter direkt zu parsen: /newgame 6 20 3h
    if len(context.args) >= 3:
        try:
            num_players = int(context.args[0])
            buyin = float(context.args[1])
            duration_str = context.args[2].lower().replace("h", "").replace("std", "")
            duration_hours = float(duration_str)
            total_minutes = int(duration_hours * 60)

            await _setup_tournament(update, num_players, buyin, total_minutes)
            return
        except (ValueError, IndexError):
            pass  # Fallthrough zu Hilfe-Text

    # Keine vollständigen Args → Anleitung zeigen
    text = (
        "🎰 *Neues Turnier einrichten*\n\n"
        "Benutze diesen Befehl mit allen Parametern:\n"
        "`/newgame [Spieler] [Buy-In €] [Dauer]`\n\n"
        "📌 *Beispiele:*\n"
        "`/newgame 6 20 3h`  — 6 Spieler, 20€, 3 Stunden\n"
        "`/newgame 8 15 2h`  — 8 Spieler, 15€, 2 Stunden\n"
        "`/newgame 5 25 2.5h` — 5 Spieler, 25€, 2,5 Stunden\n\n"
        "Der Bot berechnet dann automatisch:\n"
        "✅ Chip-Verteilung\n"
        "✅ Payout-Struktur\n"
        "✅ Blind-Level mit optimalen Zeiten\n"
        "✅ Erste 3 Level etwas länger für mehr Spielspaß"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def _setup_tournament(update: Update, num_players: int, buyin: float, total_minutes: int):
    """Internes Setup nach Parametereingabe."""
    # Blind-Struktur berechnen
    levels = build_blind_levels(total_minutes)
    db_set("blind_levels", json.dumps(levels))
    db_set("buyin_amount", str(buyin))

    total_pot = buyin * num_players
    structure = get_payout_structure(num_players)
    duration_h = total_minutes / 60

    # Payout-Zeilen
    payout_lines = []
    place_map = ["🥇", "🥈", "🥉", "4️⃣"]
    for entry in structure:
        amount = total_pot * entry["percent"] / 100
        emoji = place_map[entry["place"] - 1]
        payout_lines.append(f"{emoji} Platz {entry['place']}: {entry['percent']}% = *{amount:.2f}€*")

    # Blind-Vorschau (erste 4 Level)
    blind_preview = []
    for lvl in levels[:4]:
        marker = "🔒 _länger_" if lvl["level"] <= 3 else ""
        blind_preview.append(
            f"  Level {lvl['level']}: {lvl['small']:,}/{lvl['big']:,} — {lvl['minutes']} min {marker}"
        )
    blind_preview.append(f"  ... ({len(levels)} Level gesamt)")

    total_blind_time = sum(l["minutes"] for l in levels)

    text = (
        f"🎰 *Turnier eingerichtet!*\n\n"
        f"👥 Spieler: *{num_players}*\n"
        f"💶 Buy-In: *{buyin:.0f}€* | Pot: *{total_pot:.0f}€*\n"
        f"⏱ Dauer: *{duration_h:.1f}h* ({total_minutes} min)\n\n"
        f"💰 *Payout-Struktur:*\n" + "\n".join(payout_lines) + "\n\n"
        f"🎯 *Blind-Struktur (Auto-berechnet):*\n" + "\n".join(blind_preview) + "\n"
        f"  _Geplante Gesamtzeit: {total_blind_time} min_\n\n"
        f"📋 *Nächste Schritte:*\n"
        f"1️⃣ /addplayer [Name] — Spieler hinzufügen\n"
        f"2️⃣ /setchips 25:100 100:50 500:20 — Chips konfigurieren\n"
        f"3️⃣ /shotclock — Turnier & Timer starten\n"
        f"4️⃣ /blindstructure — Alle Level anzeigen"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ─── CHIP CONFIGURATION ─────────────────────
async def chips_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start chip configuration."""
    text = (
        "🎯 *Chip-Konfiguration*\n\n"
        "Gib deine Chips im Format ein:\n"
        "`/setchips 25:100 100:50 500:20 1000:10`\n\n"
        "Format: `Wert:Anzahl` (mit Leerzeichen getrennt)\n\n"
        "Beispiel für typisches Set:\n"
        "• 25er Chips: 100 Stück\n"
        "• 100er Chips: 50 Stück\n"
        "• 500er Chips: 20 Stück\n"
        "• 1000er Chips: 10 Stück\n\n"
        "Befehl: `/setchips 25:100 100:50 500:20 1000:10`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def set_chips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Parse and save chip configuration."""
    if not context.args:
        await update.message.reply_text(
            "❌ Bitte Chips angeben:\n`/setchips 25:100 100:50 500:20 1000:10`",
            parse_mode="Markdown"
        )
        return

    chip_config = {}
    try:
        for item in context.args:
            parts = item.split(":")
            if len(parts) != 2:
                raise ValueError(f"Ungültiges Format: {item}")
            denom, count = int(parts[0]), int(parts[1])
            chip_config[str(denom)] = count
    except ValueError as e:
        await update.message.reply_text(f"❌ Fehler: {e}\nFormat: `Wert:Anzahl`", parse_mode="Markdown")
        return

    db_set("chip_config", json.dumps(chip_config))

    # Show summary
    total_value = sum(int(d) * c for d, c in chip_config.items())
    lines = [f"  • {d}er Chips: {c} Stück = {int(d)*c:,} Chips" for d, c in sorted(chip_config.items(), key=lambda x: int(x[0]))]
    text = (
        "✅ *Chips gespeichert!*\n\n"
        + "\n".join(lines)
        + f"\n\n💰 *Gesamtwert: {total_value:,} Chips*\n\n"
        "_Jetzt Spieler mit /addplayer hinzufügen und dann /calculate aufrufen!_"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def calculate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Calculate chip distribution per player."""
    chip_config_str = db_get("chip_config")
    active_players_str = db_get("active_players")

    if not chip_config_str:
        await update.message.reply_text("❌ Erst Chips konfigurieren: /setchips")
        return
    if not active_players_str:
        await update.message.reply_text("❌ Erst Spieler hinzufügen: /addplayer [Name]")
        return

    chip_config = json.loads(chip_config_str)
    players = json.loads(active_players_str)
    num_players = len(players)

    result = calculate_chip_distribution(chip_config, num_players)

    lines = []
    for denom, count in sorted(result["chips_per_player"].items(), key=lambda x: int(x[0])):
        lines.append(f"  • {denom}er Chips: {count} Stück")

    text = (
        f"🎯 *Chip-Verteilung für {num_players} Spieler:*\n\n"
        + "\n".join(lines)
        + f"\n\n💰 *Startwert je Spieler: {result['value_per_player']:,} Chips*"
        + f"\n📊 *Gesamtwert aller Chips: {result['total_value']:,} Chips*"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ─── PLAYER MANAGEMENT ──────────────────────
async def add_player(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a player to the current tournament."""
    if not context.args:
        await update.message.reply_text("❌ Bitte Namen angeben: `/addplayer Max`", parse_mode="Markdown")
        return

    name = " ".join(context.args).strip()

    # Add to DB if not exists
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO players (name) VALUES (?)", (name,))
    conn.commit()
    conn.close()

    # Add to active session
    active_str = db_get("active_players")
    players = json.loads(active_str) if active_str else []
    if name in players:
        await update.message.reply_text(f"ℹ️ {name} ist bereits dabei!")
        return
    players.append(name)
    db_set("active_players", json.dumps(players))

    await update.message.reply_text(
        f"✅ *{name}* wurde hinzugefügt!\n"
        f"👥 Spieler bisher: {', '.join(players)} ({len(players)} gesamt)",
        parse_mode="Markdown"
    )


async def remove_player(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a player from current session."""
    if not context.args:
        await update.message.reply_text("❌ Bitte Namen angeben: `/removeplayer Max`", parse_mode="Markdown")
        return

    name = " ".join(context.args).strip()
    active_str = db_get("active_players")
    players = json.loads(active_str) if active_str else []

    if name not in players:
        await update.message.reply_text(f"❌ {name} nicht in der aktuellen Runde gefunden.")
        return

    players.remove(name)
    db_set("active_players", json.dumps(players))
    await update.message.reply_text(f"✅ {name} wurde entfernt. Noch {len(players)} Spieler.")


async def list_players(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current tournament players."""
    active_str = db_get("active_players")
    players = json.loads(active_str) if active_str else []

    if not players:
        await update.message.reply_text("👥 Noch keine Spieler. Hinzufügen mit: `/addplayer [Name]`", parse_mode="Markdown")
        return

    lines = [f"  {i+1}. {p}" for i, p in enumerate(players)]
    await update.message.reply_text(
        f"👥 *Aktuelle Spieler ({len(players)}):*\n\n" + "\n".join(lines),
        parse_mode="Markdown"
    )


# ─── PAYOUT CALCULATOR ──────────────────────
async def payout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Calculate payouts for current tournament."""
    active_str = db_get("active_players")
    players = json.loads(active_str) if active_str else []

    if not players:
        await update.message.reply_text("❌ Keine Spieler vorhanden. /addplayer benutzen.")
        return

    # Ask for buy-in if not set
    buyin_str = db_get("buyin_amount")
    if not buyin_str:
        if not context.args:
            await update.message.reply_text(
                "💶 Bitte Buy-In Betrag angeben:\n`/payout 20`\n_(z.B. 20€ Buy-In)_",
                parse_mode="Markdown"
            )
            return
        try:
            buyin = float(context.args[0])
            db_set("buyin_amount", str(buyin))
        except ValueError:
            await update.message.reply_text("❌ Ungültiger Betrag.")
            return
    else:
        buyin = float(buyin_str)
        if context.args:
            try:
                buyin = float(context.args[0])
                db_set("buyin_amount", str(buyin))
            except ValueError:
                pass

    num_players = len(players)
    total_pot = buyin * num_players
    structure = get_payout_structure(num_players)

    lines = [f"👥 Spieler: {num_players}  |  💶 Buy-In: {buyin:.0f}€  |  🏆 Pot: {total_pot:.0f}€\n"]
    for entry in structure:
        amount = total_pot * entry["percent"] / 100
        place_emoji = ["🥇", "🥈", "🥉", "4️⃣"][entry["place"] - 1]
        lines.append(f"{place_emoji} Platz {entry['place']}: {entry['percent']}% = *{amount:.2f}€*")

    special = ""
    if num_players == 6:
        amount_3rd = total_pot * 0.20
        special = f"\n\n_ℹ️ Bei 6 Spielern bekommt Platz 3 ca. den Buy-In zurück ({amount_3rd:.2f}€)_"

    await update.message.reply_text(
        "💰 *Payout-Berechnung*\n\n" + "\n".join(lines) + special,
        parse_mode="Markdown"
    )


# ─── BUSTOUT MENU ────────────────────────────
async def bustout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show bustout button menu — tap a player to eliminate them."""
    active_str = db_get("active_players")
    players = json.loads(active_str) if active_str else []
    busted_str = db_get("busted_players")
    busted = json.loads(busted_str) if busted_str else []  # [{name, place}]

    remaining = [p for p in players if p not in [b["name"] for b in busted]]

    if not remaining:
        await update.message.reply_text("❌ Keine aktiven Spieler mehr. Turnier beenden mit /endtournament")
        return

    busted_count = len(busted)
    total = len(players)

    # Buttons: eine Reihe pro Spieler
    keyboard = []
    for name in remaining:
        keyboard.append([InlineKeyboardButton(
            f"💀 {name} ausscheiden",
            callback_data=f"bust_{name}"
        )])
    keyboard.append([InlineKeyboardButton("🏁 Turnier beenden", callback_data="finish_tournament")])

    text = (
        f"💀 *Bustout-Menü*\n\n"
        f"Noch im Spiel: *{len(remaining)}* von {total} Spielern\n"
        f"Bereits ausgeschieden: {busted_count}\n\n"
        f"Wer scheidet als nächstes aus?"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def _handle_bustout(query, name: str, context: ContextTypes.DEFAULT_TYPE):
    """Process a single bustout."""
    active_str = db_get("active_players")
    players = json.loads(active_str) if active_str else []
    busted_str = db_get("busted_players")
    busted = json.loads(busted_str) if busted_str else []

    already_busted = [b["name"] for b in busted]
    if name in already_busted:
        await query.answer("Bereits ausgeschieden!")
        return

    busted_count = len(busted)
    place = len(players) - busted_count  # Letzter verbleibender = höchster Platz zuerst
    busted.append({"name": name, "place": place})
    db_set("busted_players", json.dumps(busted))

    remaining = [p for p in players if p not in [b["name"] for b in busted]]

    await query.answer(f"💀 {name} ausgeschieden (Platz {place})")

    if len(remaining) == 1:
        # Automatisch beenden wenn nur noch einer übrig
        winner = remaining[0]
        busted.append({"name": winner, "place": 1})
        db_set("busted_players", json.dumps(busted))
        await query.edit_message_text(
            f"🏆 *{winner} gewinnt das Turnier!*\n\n"
            f"💀 Letzter Bustout: {name} (Platz {place})\n\n"
            f"_Tippe /endtournament um das Ergebnis zu speichern und die Auswertung zu sehen._",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏁 Auswertung & Statistik", callback_data="finish_tournament")
            ]])
        )
        return

    # Aktualisiertes Menü
    keyboard = []
    for p in remaining:
        keyboard.append([InlineKeyboardButton(f"💀 {p} ausscheiden", callback_data=f"bust_{p}")])
    keyboard.append([InlineKeyboardButton("🏁 Turnier beenden", callback_data="finish_tournament")])

    busted_lines = []
    place_map = {1: "🥇", 2: "🥈", 3: "🥉"}
    for b in sorted(busted, key=lambda x: x["place"]):
        e = place_map.get(b["place"], f"{b['place']}.")
        busted_lines.append(f"  {e} {b['name']}")

    text = (
        f"💀 *{name}* scheidet aus — Platz {place}!\n\n"
        f"Noch im Spiel ({len(remaining)}): {', '.join(remaining)}\n\n"
        f"Bereits ausgeschieden:\n" + "\n".join(busted_lines) + "\n\n"
        f"Wer scheidet als nächstes aus?"
    )
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


# ─── END TOURNAMENT + CHART ─────────────────
async def end_tournament(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Finalize tournament, save results, send winner card + stats chart."""
    await _finalize_tournament(update.message.reply_text, context)


async def _finalize_tournament(reply_fn, context: ContextTypes.DEFAULT_TYPE):
    """Core logic: save results to DB, generate winner message + profit chart."""
    active_str = db_get("active_players")
    players = json.loads(active_str) if active_str else []
    busted_str = db_get("busted_players")
    busted = json.loads(busted_str) if busted_str else []
    buyin_str = db_get("buyin_amount")
    buyin = float(buyin_str) if buyin_str else 0.0

    if not players:
        await reply_fn("❌ Keine aktiven Spieler. Zuerst /newgame und /addplayer benutzen.")
        return

    # Falls bustout-Reihenfolge vorhanden → nutzen; sonst alphabetisch
    if busted:
        ordered = sorted(busted, key=lambda x: x["place"])
        placements = [b["name"] for b in ordered]
        # Fehlende Spieler (nie bustout eingetragen) ans Ende
        for p in players:
            if p not in placements:
                placements.append(p)
    else:
        placements = players

    num_players = len(players)
    total_pot = buyin * num_players
    structure = get_payout_structure(num_players)
    payouts = {e["place"]: total_pot * e["percent"] / 100 for e in structure}

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    chip_config_str = db_get("chip_config") or "{}"
    c.execute(
        "INSERT INTO tournaments (num_players, buyin_amount, total_pot, chip_config, status) VALUES (?,?,?,?,?)",
        (num_players, buyin, total_pot, chip_config_str, "finished")
    )
    tournament_id = c.lastrowid

    place_emoji = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    result_lines = []
    winner_name = placements[0] if placements else "?"
    winner_payout = payouts.get(1, 0.0)

    for i, name in enumerate(placements):
        place = i + 1
        payout = payouts.get(place, 0.0)
        profit = payout - buyin

        c.execute("INSERT OR IGNORE INTO players (name) VALUES (?)", (name,))
        c.execute(
            "INSERT INTO results (tournament_id, player_name, place, payout) VALUES (?,?,?,?)",
            (tournament_id, name, place, payout)
        )
        c.execute("""
            UPDATE players SET
                total_tournaments = total_tournaments + 1,
                total_wins = total_wins + ?,
                total_earnings = total_earnings + ?,
                total_buyins = total_buyins + ?
            WHERE name = ?
        """, (1 if place == 1 else 0, payout, buyin, name))

        emoji = place_emoji[i] if i < len(place_emoji) else f"{place}."
        if payout > 0:
            profit_str = f"+{profit:.2f}€" if profit >= 0 else f"{profit:.2f}€"
            result_lines.append(f"{emoji} *{name}* — {payout:.2f}€ ({profit_str})")
        else:
            result_lines.append(f"{emoji} {name} — -{buyin:.2f}€")

    conn.commit()
    conn.close()

    # ── Sieger-Nachricht ──
    winner_profit = winner_payout - buyin
    winner_roi = (winner_profit / buyin * 100) if buyin > 0 else 0

    winner_msg = (
        f"🏆 *TURNIER BEENDET! #{tournament_id}*\n\n"
        f"👑 *SIEGER: {winner_name}*\n"
        f"💰 Gewinn: *{winner_payout:.2f}€*  |  Buy-In: {buyin:.2f}€\n"
        f"📈 ROI: *+{winner_roi:.0f}%*  |  Profit: *+{winner_profit:.2f}€*\n\n"
        f"📋 *Ergebnis:*\n" + "\n".join(result_lines) + f"\n\n"
        f"💵 Gesamtpot: {total_pot:.2f}€  |  {num_players} Spieler"
    )
    await reply_fn(winner_msg, parse_mode="Markdown")

    # ── Grafik generieren ──
    chat_id = db_get("main_chat_id")
    if chat_id:
        chart_path = await _generate_profit_chart(placements, payouts, buyin, tournament_id)
        if chart_path:
            with open(chart_path, "rb") as f:
                await context.bot.send_photo(
                    chat_id=int(chat_id),
                    photo=f,
                    caption=f"📊 Turnier #{tournament_id} — Profit/Verlust je Spieler"
                )
            os.remove(chart_path)

    # ── Individuelle Stats für jeden Spieler ──
    await _send_all_player_summaries(context, placements, payouts, buyin, tournament_id)

    # Session leeren
    for key in ["active_players", "buyin_amount", "chip_config",
                "current_blind_level", "blind_start_time", "blind_running",
                "busted_players", "blind_levels"]:
        db_del(key)


async def _generate_profit_chart(placements: list, payouts: dict, buyin: float, tournament_id: int) -> Optional[str]:
    """Generate a bar chart image showing profit/loss per player."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        import numpy as np
    except ImportError:
        return None  # matplotlib not installed → skip chart

    names = placements
    profits = []
    for i, name in enumerate(names):
        place = i + 1
        payout = payouts.get(place, 0.0)
        profits.append(payout - buyin)

    colors = ["#2ecc71" if p >= 0 else "#e74c3c" for p in profits]

    fig, ax = plt.subplots(figsize=(max(8, len(names) * 1.2), 5))
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#16213e")

    bars = ax.bar(names, profits, color=colors, edgecolor="#0f3460", linewidth=1.5, width=0.6)

    # Value labels on bars
    for bar, profit in zip(bars, profits):
        ypos = bar.get_height() + (0.5 if profit >= 0 else -1.5)
        label = f"+{profit:.0f}€" if profit >= 0 else f"{profit:.0f}€"
        ax.text(bar.get_x() + bar.get_width() / 2, ypos, label,
                ha="center", va="bottom", fontsize=10, fontweight="bold",
                color="#f0f0f0")

    ax.axhline(0, color="#aaaaaa", linewidth=0.8, linestyle="--")
    ax.set_title(f"🃏 Turnier #{tournament_id} — Profit / Verlust", color="#f0f0f0",
                 fontsize=14, fontweight="bold", pad=15)
    ax.set_ylabel("€ Profit", color="#aaaaaa")
    ax.tick_params(colors="#cccccc", labelsize=10)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for spine in ["left", "bottom"]:
        ax.spines[spine].set_color("#444466")

    plt.xticks(rotation=20 if len(names) > 5 else 0)
    plt.tight_layout()

    path = f"/tmp/poker_chart_{tournament_id}.png"
    plt.savefig(path, dpi=150, facecolor=fig.get_facecolor())
    plt.close()
    return path


async def _send_all_player_summaries(context, placements, payouts, buyin, tournament_id):
    """Send a short personal summary for each player after tournament ends."""
    chat_id = db_get("main_chat_id")
    if not chat_id:
        return

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    for i, name in enumerate(placements):
        place = i + 1
        payout = payouts.get(place, 0.0)
        profit_this = payout - buyin

        c.execute("""
            SELECT total_tournaments, total_wins, total_earnings, total_buyins
            FROM players WHERE name = ?
        """, (name,))
        row = c.fetchone()
        if not row:
            continue

        tournaments, wins, total_earnings, total_buyins = row
        total_profit = total_earnings - total_buyins
        roi = (total_profit / total_buyins * 100) if total_buyins > 0 else 0
        win_rate = (wins / tournaments * 100) if tournaments > 0 else 0

        place_map = {1: "🥇", 2: "🥈", 3: "🥉"}
        place_emoji = place_map.get(place, f"#{place}")
        profit_str = f"+{profit_this:.2f}€" if profit_this >= 0 else f"{profit_this:.2f}€"
        total_str = f"+{total_profit:.2f}€" if total_profit >= 0 else f"{total_profit:.2f}€"
        roi_str = f"+{roi:.0f}%" if roi >= 0 else f"{roi:.0f}%"

        msg = (
            f"{place_emoji} *{name}* — Turnier #{tournament_id}\n"
            f"├ Platz: {place} von {len(placements)}\n"
            f"├ Payout: {payout:.2f}€  (Buy-In: {buyin:.2f}€)\n"
            f"└ Heute: *{profit_str}*\n\n"
            f"📊 *Gesamtbilanz {name}:*\n"
            f"├ {tournaments} Turniere  |  {wins} Siege  ({win_rate:.0f}% WR)\n"
            f"├ Gesamt-Profit: *{total_str}*\n"
            f"└ ROI: *{roi_str}*"
        )
        await context.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode="Markdown")
        await asyncio.sleep(0.3)  # Kurze Pause zwischen Nachrichten

    conn.close()


# ─── STATISTICS ─────────────────────────────
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show overall leaderboard."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        SELECT name, total_tournaments, total_wins, total_earnings, total_buyins
        FROM players
        WHERE total_tournaments > 0
        ORDER BY (total_earnings - total_buyins) DESC
        LIMIT 15
    """)
    rows = c.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📊 Noch keine Turnierdaten vorhanden.")
        return

    lines = []
    medals = ["🥇", "🥈", "🥉"]
    for i, (name, tournaments, wins, earnings, buyins) in enumerate(rows):
        profit = earnings - buyins
        roi = (profit / buyins * 100) if buyins > 0 else 0
        profit_str = f"+{profit:.0f}€" if profit >= 0 else f"{profit:.0f}€"
        roi_str = f"+{roi:.0f}%" if roi >= 0 else f"{roi:.0f}%"
        medal = medals[i] if i < 3 else f"{i+1}."
        lines.append(
            f"{medal} *{name}*\n"
            f"   🎮 {tournaments}x  🏆 {wins}x  💰 {profit_str}  📈 ROI {roi_str}"
        )

    await update.message.reply_text(
        "📊 *Leaderboard — Gesamtstatistik*\n\n" + "\n\n".join(lines),
        parse_mode="Markdown"
    )


async def player_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show stats + profit chart for a specific player."""
    if not context.args:
        await update.message.reply_text("❌ Bitte Namen angeben: `/playerstats Dominik`", parse_mode="Markdown")
        return

    name = " ".join(context.args).strip()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("SELECT * FROM players WHERE name = ?", (name,))
    player = c.fetchone()
    if not player:
        await update.message.reply_text(f"❌ Spieler '{name}' nicht gefunden.")
        conn.close()
        return

    _, pname, added_at, tournaments, wins, earnings, buyins = player
    profit = earnings - buyins
    roi = (profit / buyins * 100) if buyins > 0 else 0
    win_rate = (wins / tournaments * 100) if tournaments > 0 else 0

    # All results for cumulative chart
    c.execute("""
        SELECT t.date, r.place, r.payout, t.buyin_amount
        FROM results r
        JOIN tournaments t ON r.tournament_id = t.id
        WHERE r.player_name = ?
        ORDER BY t.date ASC
    """, (name,))
    all_results = c.fetchall()
    conn.close()

    recent_lines = []
    place_map = {1: "🥇", 2: "🥈", 3: "🥉"}
    cumulative = 0.0
    cum_data = []
    for date, place, payout, bi in all_results:
        p = payout - bi
        cumulative += p
        cum_data.append(cumulative)
        emoji = place_map.get(place, f"{place}.")
        date_short = date[:10]
        profit_t = f"+{p:.0f}€" if p >= 0 else f"{p:.0f}€"
        recent_lines.append(f"  {emoji} {date_short} → Platz {place}  {profit_t}")

    profit_str = f"+{profit:.2f}€" if profit >= 0 else f"{profit:.2f}€"
    roi_str = f"+{roi:.0f}%" if roi >= 0 else f"{roi:.0f}%"

    text = (
        f"👤 *{pname}*\n\n"
        f"🎮 Turniere: {tournaments}  |  🏆 Siege: {wins}  ({win_rate:.0f}% WR)\n"
        f"💰 Gesamt-Profit: *{profit_str}*\n"
        f"📈 ROI: *{roi_str}*\n"
        f"📊 Einnahmen: {earnings:.2f}€  |  Kosten: {buyins:.2f}€\n"
    )
    if recent_lines:
        text += "\n📋 *Turnierhistorie:*\n" + "\n".join(recent_lines[-8:])

    await update.message.reply_text(text, parse_mode="Markdown")

    # Kumulativer Profit-Verlauf als Grafik
    if len(cum_data) >= 2:
        chart_path = _generate_player_chart(pname, cum_data)
        if chart_path:
            with open(chart_path, "rb") as f:
                await update.message.reply_photo(
                    photo=f,
                    caption=f"📈 Kumulativer Profit-Verlauf: {pname}"
                )
            os.remove(chart_path)


def _generate_player_chart(name: str, cum_data: list) -> Optional[str]:
    """Line chart: cumulative profit over tournaments for one player."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return None

    fig, ax = plt.subplots(figsize=(8, 4))
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#16213e")

    x = list(range(1, len(cum_data) + 1))
    color = "#2ecc71" if cum_data[-1] >= 0 else "#e74c3c"

    ax.plot(x, cum_data, color=color, linewidth=2.5, marker="o", markersize=6, markerfacecolor="white")
    ax.fill_between(x, cum_data, 0, alpha=0.15, color=color)
    ax.axhline(0, color="#aaaaaa", linewidth=0.8, linestyle="--")

    # Label on last point
    last = cum_data[-1]
    label = f"+{last:.0f}€" if last >= 0 else f"{last:.0f}€"
    ax.annotate(label, xy=(x[-1], last), xytext=(8, 0), textcoords="offset points",
                color="white", fontsize=11, fontweight="bold")

    ax.set_title(f"📈 {name} — Kumulativer Profit", color="#f0f0f0", fontsize=13, fontweight="bold")
    ax.set_xlabel("Turnier #", color="#aaaaaa")
    ax.set_ylabel("€ Profit", color="#aaaaaa")
    ax.tick_params(colors="#cccccc")
    ax.xaxis.set_major_locator(plt.MaxNLocator(integer=True))
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for spine in ["left", "bottom"]:
        ax.spines[spine].set_color("#444466")

    plt.tight_layout()
    path = f"/tmp/poker_player_{name.replace(' ', '_')}.png"
    plt.savefig(path, dpi=150, facecolor=fig.get_facecolor())
    plt.close()
    return path


# ─── SHOT CLOCK / BLIND TIMER ────────────────
async def shotclock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the blind level timer."""
    db_set("current_blind_level", "1")
    db_set("blind_start_time", datetime.now().isoformat())
    db_set("blind_running", "1")

    level = get_active_blind_levels()[0]
    keyboard = [
        [
            InlineKeyboardButton("⏭ Nächstes Level", callback_data="next_level"),
            InlineKeyboardButton("⏹ Stoppen", callback_data="stop_blind"),
        ],
        [
            InlineKeyboardButton("⏱ Verbleibende Zeit", callback_data="time_left"),
            InlineKeyboardButton("📋 Alle Levels", callback_data="all_levels"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        f"⏱ *Shot Clock gestartet!*\n\n"
        f"*Level {level['level']}*\n"
        f"🔵 Small Blind: {level['small']:,}\n"
        f"🔴 Big Blind: {level['big']:,}\n"
        f"⏰ Dauer: {level['minutes']} Minuten\n\n"
        f"_Level endet um: {(datetime.now() + timedelta(minutes=level['minutes'])).strftime('%H:%M:%S')}_"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)


async def next_level_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Advance to next blind level (command version)."""
    current_str = db_get("current_blind_level")
    current = int(current_str) if current_str else 1
    next_level_num = current + 1

    if next_level_num > len(get_active_blind_levels()):
        await update.message.reply_text("🏁 Letztes Blind-Level erreicht!")
        return

    db_set("current_blind_level", str(next_level_num))
    db_set("blind_start_time", datetime.now().isoformat())

    level = get_active_blind_levels()[next_level_num - 1]
    prev_level = get_active_blind_levels()[current - 1]

    keyboard = [
        [
            InlineKeyboardButton("⏭ Nächstes Level", callback_data="next_level"),
            InlineKeyboardButton("⏹ Stoppen", callback_data="stop_blind"),
        ],
        [InlineKeyboardButton("⏱ Verbleibende Zeit", callback_data="time_left")],
    ]

    text = (
        f"⏭ *Level {next_level_num} — Blinds erhöht!*\n\n"
        f"War: {prev_level['small']:,} / {prev_level['big']:,}\n"
        f"Jetzt: *{level['small']:,} / {level['big']:,}*\n\n"
        f"⏰ Dauer: {level['minutes']} Minuten\n"
        f"_Ende um: {(datetime.now() + timedelta(minutes=level['minutes'])).strftime('%H:%M:%S')}_"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def stop_blind_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_set("blind_running", "0")
    await update.message.reply_text("⏹ Blind-Timer gestoppt. Mit /shotclock neu starten.")


async def blinds_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current blind level and time remaining."""
    current_str = db_get("current_blind_level")
    start_str = db_get("blind_start_time")
    running = db_get("blind_running")

    if not current_str or running != "1":
        await update.message.reply_text("⏹ Kein aktiver Blind-Timer. Starten mit /shotclock")
        return

    current = int(current_str)
    level = get_active_blind_levels()[current - 1]

    elapsed = timedelta(0)
    if start_str:
        start_time = datetime.fromisoformat(start_str)
        elapsed = datetime.now() - start_time

    remaining = timedelta(minutes=level["minutes"]) - elapsed
    if remaining.total_seconds() < 0:
        remaining = timedelta(0)

    mins = int(remaining.total_seconds() // 60)
    secs = int(remaining.total_seconds() % 60)

    keyboard = [[
        InlineKeyboardButton("⏭ Nächstes Level", callback_data="next_level"),
        InlineKeyboardButton("⏱ Aktualisieren", callback_data="time_left"),
    ]]

    text = (
        f"⏱ *Level {current} — Aktuelle Blinds*\n\n"
        f"🔵 Small Blind: *{level['small']:,}*\n"
        f"🔴 Big Blind: *{level['big']:,}*\n\n"
        f"⏰ Verbleibend: *{mins}:{secs:02d}*\n"
    )

    if current < len(get_active_blind_levels()):
        next_l = get_active_blind_levels()[current]
        text += f"\n_Nächstes Level: {next_l['small']:,} / {next_l['big']:,}_"

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def blind_structure_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show full blind structure."""
    current_str = db_get("current_blind_level")
    current = int(current_str) if current_str else 0

    lines = []
    for lvl in get_active_blind_levels():
        marker = " ◀️" if lvl["level"] == current else ""
        lines.append(
            f"Level {lvl['level']:2d} | SB: {lvl['small']:>5,} | BB: {lvl['big']:>6,} | {lvl['minutes']} min{marker}"
        )

    await update.message.reply_text(
        "📋 *Blind-Struktur:*\n\n`" + "\n".join(lines) + "`",
        parse_mode="Markdown"
    )


# ─── STATUS ─────────────────────────────────
async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current tournament status."""
    active_str = db_get("active_players")
    players = json.loads(active_str) if active_str else []
    chip_config_str = db_get("chip_config")
    buyin_str = db_get("buyin_amount")
    current_blind = db_get("current_blind_level")
    blind_running = db_get("blind_running")

    text = "📊 *Aktueller Status*\n\n"

    if players:
        text += f"👥 Spieler ({len(players)}): {', '.join(players)}\n"
    else:
        text += "👥 Keine aktiven Spieler\n"

    if buyin_str:
        buyin = float(buyin_str)
        pot = buyin * len(players)
        text += f"💶 Buy-In: {buyin:.0f}€ | Pot: {pot:.0f}€\n"
    else:
        text += "💶 Buy-In: nicht gesetzt\n"

    if chip_config_str:
        cfg = json.loads(chip_config_str)
        total = sum(int(d) * c for d, c in cfg.items())
        text += f"🎯 Chips konfiguriert (Gesamtwert: {total:,})\n"
    else:
        text += "🎯 Chips: nicht konfiguriert\n"

    if current_blind and blind_running == "1":
        lvl = get_active_blind_levels()[int(current_blind) - 1]
        text += f"⏱ Blind-Level {current_blind}: {lvl['small']:,}/{lvl['big']:,}\n"
    else:
        text += "⏱ Blind-Timer: nicht aktiv\n"

    await update.message.reply_text(text, parse_mode="Markdown")


# ─── CALLBACK QUERY HANDLER ─────────────────
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard button presses."""
    query = update.callback_query
    await query.answer()

    if query.data.startswith("bust_"):
        name = query.data[5:]
        await _handle_bustout(query, name, context)
        return

    if query.data == "finish_tournament":
        await query.edit_message_text("🏁 Turnier wird ausgewertet...")
        await _finalize_tournament(
            lambda text, **kw: context.bot.send_message(
                chat_id=query.message.chat_id, text=text, **kw
            ),
            context
        )
        return

    if query.data == "next_level":
        current_str = db_get("current_blind_level")
        current = int(current_str) if current_str else 1
        next_level_num = current + 1

        if next_level_num > len(get_active_blind_levels()):
            await query.edit_message_text("🏁 Letztes Blind-Level erreicht!")
            return

        db_set("current_blind_level", str(next_level_num))
        db_set("blind_start_time", datetime.now().isoformat())

        level = get_active_blind_levels()[next_level_num - 1]
        prev_level = get_active_blind_levels()[current - 1]

        keyboard = [[
            InlineKeyboardButton("⏭ Nächstes Level", callback_data="next_level"),
            InlineKeyboardButton("⏹ Stoppen", callback_data="stop_blind"),
        ], [InlineKeyboardButton("⏱ Zeit prüfen", callback_data="time_left")]]

        text = (
            f"⏭ *Level {next_level_num} — Blinds erhöht!*\n\n"
            f"War: {prev_level['small']:,} / {prev_level['big']:,}\n"
            f"Jetzt: *{level['small']:,} / {level['big']:,}*\n\n"
            f"⏰ Dauer: {level['minutes']} Minuten\n"
            f"_Ende um: {(datetime.now() + timedelta(minutes=level['minutes'])).strftime('%H:%M:%S')}_"
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "stop_blind":
        db_set("blind_running", "0")
        await query.edit_message_text("⏹ Blind-Timer gestoppt. Mit /shotclock neu starten.")

    elif query.data == "time_left":
        current_str = db_get("current_blind_level")
        start_str = db_get("blind_start_time")
        current = int(current_str) if current_str else 1
        level = get_active_blind_levels()[current - 1]

        elapsed = timedelta(0)
        if start_str:
            start_time = datetime.fromisoformat(start_str)
            elapsed = datetime.now() - start_time

        remaining = timedelta(minutes=level["minutes"]) - elapsed
        if remaining.total_seconds() < 0:
            remaining = timedelta(0)

        mins = int(remaining.total_seconds() // 60)
        secs = int(remaining.total_seconds() % 60)

        keyboard = [[
            InlineKeyboardButton("⏭ Nächstes Level", callback_data="next_level"),
            InlineKeyboardButton("🔄 Aktualisieren", callback_data="time_left"),
        ]]

        text = (
            f"⏱ *Level {current}*\n\n"
            f"🔵 Small Blind: *{level['small']:,}*\n"
            f"🔴 Big Blind: *{level['big']:,}*\n\n"
            f"⏰ Verbleibend: *{mins}:{secs:02d}*"
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "all_levels":
        current_str = db_get("current_blind_level")
        current = int(current_str) if current_str else 0
        lines = []
        for lvl in get_active_blind_levels():
            marker = " ◀️" if lvl["level"] == current else ""
            lines.append(f"Lvl {lvl['level']:2d} | {lvl['small']:>5,}/{lvl['big']:>6,} | {lvl['minutes']}min{marker}")
        await query.edit_message_text("📋 *Blind-Struktur:*\n\n`" + "\n".join(lines) + "`", parse_mode="Markdown")


# ─────────────────────────────────────────────
#  AUTO BLIND LEVEL NOTIFICATION
# ─────────────────────────────────────────────
async def check_blind_timer(context: ContextTypes.DEFAULT_TYPE):
    """Background job: notify when blind level expires."""
    running = db_get("blind_running")
    if running != "1":
        return

    current_str = db_get("current_blind_level")
    start_str = db_get("blind_start_time")
    if not current_str or not start_str:
        return

    current = int(current_str)
    if current > len(get_active_blind_levels()):
        return

    level = get_active_blind_levels()[current - 1]
    start_time = datetime.fromisoformat(start_str)
    elapsed = datetime.now() - start_time
    level_duration = timedelta(minutes=level["minutes"])

    # Send warning 2 minutes before level ends
    two_min_before = level_duration - timedelta(minutes=2)
    warned = db_get(f"warned_level_{current}")

    if elapsed >= two_min_before and not warned:
        db_set(f"warned_level_{current}", "1")
        chat_id = db_get("main_chat_id")
        if chat_id:
            keyboard = [[InlineKeyboardButton("⏭ Jetzt Level wechseln", callback_data="next_level")]]
            await context.bot.send_message(
                chat_id=int(chat_id),
                text=f"⚠️ *Noch 2 Minuten auf Level {current}!*\nBlinds: {level['small']:,} / {level['big']:,}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    # Auto-announce when level ends
    if elapsed >= level_duration:
        next_level_num = current + 1
        if next_level_num <= len(get_active_blind_levels()):
            db_set("current_blind_level", str(next_level_num))
            db_set("blind_start_time", datetime.now().isoformat())
            db_del(f"warned_level_{current}")

            next_level = get_active_blind_levels()[next_level_num - 1]
            chat_id = db_get("main_chat_id")
            if chat_id:
                keyboard = [[
                    InlineKeyboardButton("⏭ Weiter", callback_data="next_level"),
                    InlineKeyboardButton("⏱ Zeit", callback_data="time_left"),
                ]]
                await context.bot.send_message(
                    chat_id=int(chat_id),
                    text=(
                        f"🔔 *BLINDS ERHÖHT! — Level {next_level_num}*\n\n"
                        f"🔵 Small Blind: *{next_level['small']:,}*\n"
                        f"🔴 Big Blind: *{next_level['big']:,}*\n"
                        f"⏰ Dauer: {next_level['minutes']} Minuten"
                    ),
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )


async def save_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save chat ID when any message is sent (for auto-notifications)."""
    chat_id = str(update.effective_chat.id)
    if db_get("main_chat_id") != chat_id:
        db_set("main_chat_id", chat_id)


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Bitte BOT_TOKEN in der Datei setzen!")
        print("   Token bekommst du von @BotFather auf Telegram")
        return

    logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("newgame", new_game))
    app.add_handler(CommandHandler("chips", chips_cmd))
    app.add_handler(CommandHandler("setchips", set_chips))
    app.add_handler(CommandHandler("calculate", calculate))
    app.add_handler(CommandHandler("addplayer", add_player))
    app.add_handler(CommandHandler("removeplayer", remove_player))
    app.add_handler(CommandHandler("players", list_players))
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

    # Callback buttons
    app.add_handler(CallbackQueryHandler(button_callback))

    # Auto-save chat ID
    app.add_handler(MessageHandler(filters.ALL, save_chat_id))

    # Background job: check blind timer every 30 seconds
    app.job_queue.run_repeating(check_blind_timer, interval=30, first=10)

    print("🃏 Poker Bot gestartet! Drücke Ctrl+C zum Beenden.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()