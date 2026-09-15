import asyncio
import logging
import os
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)

MIN_SCORE = int(os.getenv("MIN_SCORE", "75"))
AUTO_SCAN_SECONDS = int(os.getenv("AUTO_SCAN_SECONDS", "30"))

DB_FILE = os.getenv("DB_FILE", "bot.db")

MSK = ZoneInfo("Europe/Moscow")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("pocket_otc_bot")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()


# ============================================================
# DATABASE
# ============================================================

db = sqlite3.connect(DB_FILE, check_same_thread=False)
db.row_factory = sqlite3.Row


def init_db():
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            joined_at TEXT NOT NULL,
            auto_signals INTEGER DEFAULT 0,
            pair TEXT DEFAULT 'ALL',
            duration INTEGER DEFAULT 5,
            approved INTEGER DEFAULT 1,
            banned INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            pair TEXT NOT NULL,
            direction TEXT NOT NULL,
            score INTEGER NOT NULL,
            duration INTEGER NOT NULL,
            entry_time TEXT NOT NULL,
            exit_time TEXT NOT NULL,
            result TEXT DEFAULT 'PENDING',
            user_entered INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER,
            user_id INTEGER,
            feedback TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )

    db.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES('min_score', ?)",
        (str(MIN_SCORE),),
    )
    db.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES('auto_enabled', '1')"
    )
    db.commit()


def now_utc():
    return datetime.now(timezone.utc)


def now_msk():
    return datetime.now(MSK)


def get_setting(key, default=None):
    row = db.execute(
        "SELECT value FROM settings WHERE key = ?",
        (key,),
    ).fetchone()

    return row["value"] if row else default


def set_setting(key, value):
    db.execute(
        """
        INSERT INTO settings(key, value)
        VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, str(value)),
    )
    db.commit()


def ensure_user(user):
    db.execute(
        """
        INSERT INTO users(
            user_id,
            username,
            first_name,
            joined_at
        )
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name
        """,
        (
            user.id,
            user.username or "",
            user.first_name or "",
            now_utc().isoformat(),
        ),
    )
    db.commit()


def is_banned(user_id):
    row = db.execute(
        "SELECT banned FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    return bool(row and row["banned"])


def get_user(user_id):
    return db.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()


# ============================================================
# KEYBOARDS
# ============================================================

def main_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎯 Получить сигнал",
                    callback_data="signal",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🤖 Автосигналы",
                    callback_data="auto",
                ),
                InlineKeyboardButton(
                    text="💱 Пара",
                    callback_data="pairs",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⏱ Время",
                    callback_data="duration",
                ),
                InlineKeyboardButton(
                    text="📊 Статистика",
                    callback_data="stats",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👤 Профиль",
                    callback_data="profile",
                )
            ],
        ]
    )


def pair_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🌐 Все OTC",
                    callback_data="pair:ALL",
                )
            ],
            [
                InlineKeyboardButton(
                    text="EUR/USD OTC",
                    callback_data="pair:EUR/USD OTC",
                ),
                InlineKeyboardButton(
                    text="GBP/USD OTC",
                    callback_data="pair:GBP/USD OTC",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="USD/JPY OTC",
                    callback_data="pair:USD/JPY OTC",
                ),
                InlineKeyboardButton(
                    text="AUD/USD OTC",
                    callback_data="pair:AUD/USD OTC",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data="menu",
                )
            ],
        ]
    )


def duration_keyboard():
    rows = []

    for start in range(1, 21, 4):
        row = []

        for value in range(start, min(start + 4, 21)):
            row.append(
                InlineKeyboardButton(
                    text=f"{value} мин",
                    callback_data=f"duration:{value}",
                )
            )

        rows.append(row)

    rows.append(
        [
            InlineKeyboardButton(
                text="🎲 Любое время",
                callback_data="duration:any",
            )
        ]
    )

    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="menu",
            )
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def signal_buttons(signal_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ ЗАШЁЛ В СИГНАЛ",
                    callback_data=f"entered:{signal_id}",
                ),
                InlineKeyboardButton(
                    text="❌ НЕ ЗАШЁЛ",
                    callback_data=f"not_entered:{signal_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🟢 СИГНАЛ ВЕРНЫЙ",
                    callback_data=f"correct:{signal_id}",
                ),
                InlineKeyboardButton(
                    text="🔴 СИГНАЛ НЕВЕРНЫЙ",
                    callback_data=f"wrong:{signal_id}",
                ),
            ],
        ]
    )


def owner_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 Статистика",
                    callback_data="owner:stats",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎯 Мин. Score",
                    callback_data="owner:score",
                ),
                InlineKeyboardButton(
                    text="🤖 Автосигналы",
                    callback_data="owner:auto",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👥 Пользователи",
                    callback_data="owner:users",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ Настройки",
                    callback_data="owner:settings",
                )
            ],
        ]
    )


# ============================================================
# TIME
# ============================================================

def next_entry_time(minutes=1):
    """
    Entry is always in the future.
    We deliberately don't generate an already-started signal.
    """

    current = now_msk()

    # Give Telegram/network enough time.
    entry = current + timedelta(seconds=30)

    # Align to the next minute.
    entry = entry.replace(second=0, microsecond=0)

    if entry <= current:
        entry += timedelta(minutes=1)

    return entry


def make_times(duration):
    entry = next_entry_time()

    if duration == "any":
        duration_value = random.randint(1, 20)
    else:
        duration_value = int(duration)

    exit_time = entry + timedelta(minutes=duration_value)

    return entry, exit_time, duration_value


# ============================================================
# POCKET OPTION MARKET PLACEHOLDER
# ============================================================

class PocketOptionMarket:
    """
    REAL Pocket Option market connection is intentionally not
    fabricated here.

    This class is the only place that needs to be replaced when
    we have the confirmed OTC websocket/API protocol.

    It must eventually provide:
        - available OTC pairs
        - candles
        - current prices
        - timestamps
    """

    def __init__(self):
        self.connected = False
        self.pairs = []

    async def connect(self):
        """
        TODO:
        Connect to Pocket Option using the actual supported
        protocol once it is confirmed.
        """
        self.connected = False
        return False

    async def get_pairs(self):
        if not self.connected:
            return []

        return self.pairs

    async def get_candles(self, pair, timeframe=60, limit=100):
        if not self.connected:
            return []

        return []

    async def get_price(self, pair):
        if not self.connected:
            return None

        return None


market = PocketOptionMarket()


# ============================================================
# SIGNAL ENGINE
# ============================================================

class SignalEngine:

    async def analyze(self, pair, duration):
        """
        No fake signal is generated.

        Until real Pocket Option candles are connected,
        this method returns None.
        """

        if not market.connected:
            return None

        candles = await market.get_candles(
            pair=pair,
            timeframe=60,
            limit=150,
        )

        if not candles:
            return None

        # ====================================================
        # REAL ANALYSIS WILL BE HERE
        #
        # EMA
        # RSI
        # MACD
        # STOCHASTIC
        # SUPPORT / RESISTANCE
        # PRICE ACTION
        # VOLATILITY
        # AI FILTER
        # ====================================================

        return None


engine = SignalEngine()


# ============================================================
# SIGNAL CREATION
# ============================================================

async def create_signal(user_id):
    user = get_user(user_id)

    if not user:
        return None, "Пользователь не найден."

    pair = user["pair"]
    duration = user["duration"]

    # Don't fabricate market data.
    if not market.connected:
        return (
            None,
            "⚠️ OTC-рынок пока не подключён.\n\n"
            "Я не буду выдавать случайный CALL/PUT "
            "без реальных данных Pocket Option.",
        )

    if pair == "ALL":
        pairs = await market.get_pairs()

        if not pairs:
            return None, "⚠️ Сейчас не удалось получить список OTC-пар."

        # In production this will analyze every suitable pair.
        selected_pair = pairs[0]
    else:
        selected_pair = pair

    result = await engine.analyze(
        selected_pair,
        duration,
    )

    if not result:
        return (
            None,
            "🔎 Подходящего сигнала сейчас нет.\n\n"
            f"Минимальный Quality Score: "
            f"{get_setting('min_score', MIN_SCORE)}/100\n\n"
            "Слабый сигнал отправлять не буду.",
        )

    score = int(result["score"])
    min_score = int(get_setting("min_score", MIN_SCORE))

    if score < min_score:
        return (
            None,
            f"🔎 Сигнал отфильтрован.\n\n"
            f"Score: {score}/100\n"
            f"Минимум: {min_score}/100",
        )

    entry, exit_time, duration_value = make_times(duration)

    signal = {
        "pair": selected_pair,
        "direction": result["direction"],
        "score": score,
        "duration": duration_value,
        "entry": entry,
        "exit": exit_time,
        "analysis": result,
    }

    cursor = db.execute(
        """
        INSERT INTO signals(
            user_id,
            pair,
            direction,
            score,
            duration,
            entry_time,
            exit_time,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            selected_pair,
            result["direction"],
            score,
            duration_value,
            entry.isoformat(),
            exit_time.isoformat(),
            now_utc().isoformat(),
        ),
    )

    db.commit()

    signal["id"] = cursor.lastrowid

    return signal, None


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(signal):
    direction = signal["direction"]

    if direction.upper() == "CALL":
        direction_text = "🟢 CALL / ВВЕРХ"
    else:
        direction_text = "🔴 PUT / ВНИЗ"

    analysis = signal.get("analysis", {})

    reasons = analysis.get("reasons", [])

    if reasons:
        reason_text = "\n".join(
            f"• {reason}" for reason in reasons[:8]
        )
    else:
        reason_text = "• Подробный анализ будет показан после подключения рынка."

    return (
        "🎯 <b>OTC СИГНАЛ</b>\n\n"
        f"💱 Пара: <b>{signal['pair']}</b>\n"
        f"📌 Направление: <b>{direction_text}</b>\n\n"
        f"⏰ Вход: <b>{signal['entry'].strftime('%H:%M')} МСК</b>\n"
        f"⏰ Выход: <b>{signal['exit'].strftime('%H:%M')} МСК</b>\n"
        f"⏱ Экспирация: <b>{signal['duration']} мин</b>\n\n"
        f"📊 Quality Score: <b>{signal['score']}/100</b>\n\n"
        "🔎 <b>АНАЛИЗ</b>\n"
        f"{reason_text}\n\n"
        "⚠️ Фактический winrate считается отдельно "
        "по закрытым сигналам."
    )


# ============================================================
# START
# ============================================================

@dp.message(CommandStart())
async def start_handler(message: Message):
    ensure_user(message.from_user)

    if is_banned(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return

    await message.answer(
        "🤖 <b>POCKET OTC SIGNAL BOT</b>\n\n"
        "Только OTC-пары Pocket Option.\n\n"
        "🎯 Получайте сигналы вручную\n"
        "🤖 Включайте автоматические сигналы\n"
        "📊 Смотрите статистику\n"
        "⏱ Выбирайте экспирацию 1–20 минут\n\n"
        "Сигналы ниже установленного порога "
        "отправляться не будут.",
        reply_markup=main_keyboard(),
    )


# ============================================================
# MENU
# ============================================================

@dp.callback_query(F.data == "menu")
async def menu_callback(callback: CallbackQuery):
    await callback.answer()

    await callback.message.edit_text(
        "🏠 <b>Главное меню</b>",
        reply_markup=main_keyboard(),
    )


@dp.callback_query(F.data == "pairs")
async def pairs_callback(callback: CallbackQuery):
    await callback.answer()

    user = get_user(callback.from_user.id)

    current = user["pair"] if user else "ALL"

    await callback.message.edit_text(
        "💱 <b>Выбор OTC-пары</b>\n\n"
        f"Текущий выбор: <b>{current}</b>",
        reply_markup=pair_keyboard(),
    )


@dp.callback_query(F.data.startswith("pair:"))
async def pair_callback(callback: CallbackQuery):
    await callback.answer()

    pair = callback.data.split(":", 1)[1]

    db.execute(
        "UPDATE users SET pair = ? WHERE user_id = ?",
        (pair, callback.from_user.id),
    )
    db.commit()

    await callback.message.edit_text(
        f"✅ Пара установлена:\n\n<b>{pair}</b>",
        reply_markup=main_keyboard(),
    )


@dp.callback_query(F.data == "duration")
async def duration_callback(callback: CallbackQuery):
    await callback.answer()

    await callback.message.edit_text(
        "⏱ <b>Экспирация</b>\n\n"
        "Выбери от 1 до 20 минут.\n"
        "Или выбери «Любое время», чтобы система "
        "сама выбирала подходящую экспирацию.",
        reply_markup=duration_keyboard(),
    )


@dp.callback_query(F.data.startswith("duration:"))
async def duration_select(callback: CallbackQuery):
    await callback.answer()

    value = callback.data.split(":", 1)[1]

    if value == "any":
        db.execute(
            "UPDATE users SET duration = 0 WHERE user_id = ?",
            (callback.from_user.id,),
        )
        text = "🎲 Экспирация: <b>любая</b>"
    else:
        duration = int(value)

        db.execute(
            "UPDATE users SET duration = ? WHERE user_id = ?",
            (duration, callback.from_user.id),
        )

        text = f"⏱ Экспирация: <b>{duration} мин</b>"

    db.commit()

    await callback.message.edit_text(
        f"✅ {text}",
        reply_markup=main_keyboard(),
    )


# ============================================================
# MANUAL SIGNAL
# ============================================================

@dp.callback_query(F.data == "signal")
async def signal_callback(callback: CallbackQuery):
    await callback.answer("Анализирую рынок...")

    if is_banned(callback.from_user.id):
        await callback.message.answer("⛔ Доступ запрещён.")
        return

    user = get_user(callback.from_user.id)

    if not user:
        ensure_user(callback.from_user)
        user = get_user(callback.from_user.id)

    duration = user["duration"]

    if duration == 0:
        duration_value = "any"
    else:
        duration_value = duration

    await callback.message.answer(
        "🔎 <b>Проверяю OTC-рынок...</b>\n\n"
        "Ищу только ситуации, которые проходят "
        f"минимальный фильтр {get_setting('min_score', MIN_SCORE)}/100."
    )

    # Keep the selected duration.
    old_duration = user["duration"]

    if duration_value == "any":
        pass

    signal, error = await create_signal(callback.from_user.id)

    if error:
        await callback.message.answer(error)
        return

    await callback.message.answer(
        format_signal(signal),
        reply_markup=signal_buttons(signal["id"]),
    )


# ============================================================
# AUTO SIGNALS
# ============================================================

@dp.callback_query(F.data == "auto")
async def auto_callback(callback: CallbackQuery):
    await callback.answer()

    user = get_user(callback.from_user.id)

    if not user:
        ensure_user(callback.from_user)
        user = get_user(callback.from_user.id)

    enabled = bool(user["auto_signals"])

    new_value = 0 if enabled else 1

    db.execute(
        "UPDATE users SET auto_signals = ? WHERE user_id = ?",
        (new_value, callback.from_user.id),
    )
    db.commit()

    status = "🟢 ВКЛЮЧЕНЫ" if new_value else "🔴 ВЫКЛЮЧЕНЫ"

    await callback.message.edit_text(
        "🤖 <b>Автоматические сигналы</b>\n\n"
        f"Статус: <b>{status}</b>\n\n"
        "Бот будет отправлять сигнал только тогда, "
        "когда реальный анализ рынка пройдёт установленный фильтр.",
        reply_markup=main_keyboard(),
    )


# ============================================================
# PROFILE
# ============================================================

@dp.callback_query(F.data == "profile")
async def profile_callback(callback: CallbackQuery):
    await callback.answer()

    user = get_user(callback.from_user.id)

    await callback.message.edit_text(
        "👤 <b>ПРОФИЛЬ</b>\n\n"
        f"🆔 ID: <code>{callback.from_user.id}</code>\n"
        f"👤 Username: @{callback.from_user.username or 'нет'}\n"
        f"🤖 Автосигналы: "
        f"{'🟢' if user['auto_signals'] else '🔴'}\n"
        f"💱 Пара: <b>{user['pair']}</b>\n"
        f"⏱ Экспирация: "
        f"<b>{'Любая' if user['duration'] == 0 else str(user['duration']) + ' мин'}</b>",
        reply_markup=main_keyboard(),
    )


# ============================================================
# STATISTICS
# ============================================================

@dp.callback_query(F.data == "stats")
async def stats_callback(callback: CallbackQuery):
    await callback.answer()

    total = db.execute(
        "SELECT COUNT(*) AS c FROM signals"
    ).fetchone()["c"]

    wins = db.execute(
        "SELECT COUNT(*) AS c FROM signals WHERE result = 'WIN'"
    ).fetchone()["c"]

    losses = db.execute(
        "SELECT COUNT(*) AS c FROM signals WHERE result = 'LOSS'"
    ).fetchone()["c"]

    finished = wins + losses

    winrate = (wins / finished * 100) if finished else 0

    await callback.message.edit_text(
        "📊 <b>СТАТИСТИКА</b>\n\n"
        f"Всего сигналов: <b>{total}</b>\n"
        f"🟢 WIN: <b>{wins}</b>\n"
        f"🔴 LOSS: <b>{losses}</b>\n"
        f"📈 Фактический winrate: <b>{winrate:.2f}%</b>\n\n"
        "Незавершённые сигналы не учитываются "
        "в winrate.",
        reply_markup=main_keyboard(),
    )


# ============================================================
# SIGNAL FEEDBACK
# ============================================================

@dp.callback_query(F.data.startswith("entered:"))
async def entered_callback(callback: CallbackQuery):
    await callback.answer("Записал: вы зашли в сигнал.")

    signal_id = int(callback.data.split(":")[1])

    db.execute(
        """
        UPDATE signals
        SET user_entered = 1
        WHERE id = ? AND user_id = ?
        """,
        (signal_id, callback.from_user.id),
    )
    db.commit()


@dp.callback_query(F.data.startswith("not_entered:"))
async def not_entered_callback(callback: CallbackQuery):
    await callback.answer("Записал: вы не заходили.")

    signal_id = int(callback.data.split(":")[1])

    db.execute(
        """
        UPDATE signals
        SET user_entered = 0
        WHERE id = ? AND user_id = ?
        """,
        (signal_id, callback.from_user.id),
    )
    db.commit()


@dp.callback_query(F.data.startswith("correct:"))
async def correct_callback(callback: CallbackQuery):
    await callback.answer("Спасибо, обратная связь записана.")

    signal_id = int(callback.data.split(":")[1])

    db.execute(
        """
        INSERT INTO feedback(
            signal_id,
            user_id,
            feedback,
            created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            signal_id,
            callback.from_user.id,
            "CORRECT",
            now_utc().isoformat(),
        ),
    )
    db.commit()


@dp.callback_query(F.data.startswith("wrong:"))
async def wrong_callback(callback: CallbackQuery):
    await callback.answer("Спасибо, обратная связь записана.")

    signal_id = int(callback.data.split(":")[1])

    db.execute(
        """
        INSERT INTO feedback(
            signal_id,
            user_id,
            feedback,
            created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            signal_id,
            callback.from_user.id,
            "WRONG",
            now_utc().isoformat(),
        ),
    )
    db.commit()


# ============================================================
# OWNER PANEL
# ============================================================

def owner_only(user_id):
    return OWNER_ID != 0 and user_id == OWNER_ID


@dp.message(Command("owner"))
async def owner_command(message: Message):
    ensure_user(message.from_user)

    if not owner_only(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return

    await message.answer(
        "👑 <b>OWNER PANEL</b>",
        reply_markup=owner_keyboard(),
    )


@dp.callback_query(F.data.startswith("owner:"))
async def owner_callback(callback: CallbackQuery):
    if not owner_only(callback.from_user.id):
        await callback.answer(
            "⛔ Нет доступа.",
            show_alert=True,
        )
        return

    await callback.answer()

    action = callback.data.split(":", 1)[1]

    if action == "stats":
        total = db.execute(
            "SELECT COUNT(*) AS c FROM signals"
        ).fetchone()["c"]

        wins = db.execute(
            "SELECT COUNT(*) AS c FROM signals WHERE result='WIN'"
        ).fetchone()["c"]

        losses = db.execute(
            "SELECT COUNT(*) AS c FROM signals WHERE result='LOSS'"
        ).fetchone()["c"]

        finished = wins + losses
        winrate = wins / finished * 100 if finished else 0

        await callback.message.edit_text(
            "👑 <b>СТАТИСТИКА БОТА</b>\n\n"
            f"Сигналов: {total}\n"
            f"WIN: {wins}\n"
            f"LOSS: {losses}\n"
            f"Winrate: {winrate:.2f}%",
            reply_markup=owner_keyboard(),
        )

    elif action == "score":
        await callback.message.edit_text(
            "🎯 <b>Минимальный Quality Score</b>\n\n"
            f"Сейчас: <b>{get_setting('min_score', MIN_SCORE)}/100</b>\n\n"
            "Изменение пока выполняется через Railway "
            "Environment Variable MIN_SCORE.",
            reply_markup=owner_keyboard(),
        )

    elif action == "auto":
        value = get_setting("auto_enabled", "1")

        await callback.message.edit_text(
            "🤖 <b>Автосигналы</b>\n\n"
            f"Глобально: {'🟢 ВКЛ' if value == '1' else '🔴 ВЫКЛ'}",
            reply_markup=owner_keyboard(),
        )

    elif action == "users":
        count = db.execute(
            "SELECT COUNT(*) AS c FROM users"
        ).fetchone()["c"]

        await callback.message.edit_text(
            f"👥 <b>ПОЛЬЗОВАТЕЛИ</b>\n\n"
            f"Всего: <b>{count}</b>",
            reply_markup=owner_keyboard(),
        )

    elif action == "settings":
        await callback.message.edit_text(
            "⚙️ <b>НАСТРОЙКИ</b>\n\n"
            f"MIN_SCORE: {get_setting('min_score', MIN_SCORE)}\n"
            f"AUTO_SCAN_SECONDS: {AUTO_SCAN_SECONDS}\n"
            "Часовой пояс: Europe/Moscow",
            reply_markup=owner_keyboard(),
        )


# ============================================================
# AUTO SCANNER
# ============================================================

async def auto_scanner():
    """
    Automatic signal loop.

    It intentionally does nothing until the real Pocket Option
    market connection is available.
    """

    while True:
        try:
            if market.connected:
                users = db.execute(
                    """
                    SELECT *
                    FROM users
                    WHERE auto_signals = 1
                    AND banned = 0
                    """
                ).fetchall()

                for user in users:
                    signal, error = await create_signal(
                        user["user_id"]
                    )

                    if signal:
                        try:
                            await bot.send_message(
                                user["user_id"],
                                format_signal(signal),
                                reply_markup=signal_buttons(
                                    signal["id"]
                                ),
                            )
                        except Exception:
                            logger.exception(
                                "Failed to send automatic signal"
                            )

            await asyncio.sleep(AUTO_SCAN_SECONDS)

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception("Auto scanner error")
            await asyncio.sleep(AUTO_SCAN_SECONDS)


# ============================================================
# RESULT CHECKER
# ============================================================

async def result_checker():
    """
    Once real price data is connected, this worker will check
    the market at signal exit time and mark WIN/LOSS.

    Until then it does not invent results.
    """

    while True:
        try:
            if market.connected:
                # Real result calculation will be implemented
                # using Pocket Option candle/price data.
                pass

            await asyncio.sleep(5)

        except asyncio.CancelledError:
            raise

        except Exception:
            logger.exception("Result checker error")
            await asyncio.sleep(5)


# ============================================================
# STARTUP
# ============================================================

async def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is not configured."
        )

    init_db()

    logger.info("Starting POCKET OTC SIGNAL BOT")
    logger.info(
        "Minimum score: %s",
        get_setting("min_score", MIN_SCORE),
    )

    # IMPORTANT:
    # Do not pretend that Pocket Option is connected.
    connected = await market.connect()

    if connected:
        logger.info("Pocket Option market connected")
    else:
        logger.warning(
            "Pocket Option market is NOT connected yet. "
            "No fake signals will be generated."
        )

    scanner_task = asyncio.create_task(
        auto_scanner()
    )

    result_task = asyncio.create_task(
        result_checker()
    )

    try:
        await dp.start_polling(bot)

    finally:
        scanner_task.cancel()
        result_task.cancel()

        await asyncio.gather(
            scanner_task,
            result_task,
            return_exceptions=True,
        )

        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())