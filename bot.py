import asyncio
import logging
import os
import traceback
from html import escape
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict

import aiosqlite
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, TelegramObject,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("gdz_bot")

# ============ CONFIG ============
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "0").split(",") if x.strip().isdigit()]
OWNER_ID = int(os.getenv("OWNER_ID", str(ADMIN_IDS[0] if ADMIN_IDS else 0)))
DB_PATH = os.getenv("DB_PATH", "bot.db")

CREATORS = ["@n3xyx", "@rakuzsa", "@xuzoe"]
SUPPORT_CONTACT = "@rakuzsa"

# ============ EMOJI ============
USE_PREMIUM = os.getenv("USE_PREMIUM_EMOJI", "0") == "1"
PREMIUM_IDS: dict[str, str] = {}
EMOJI = {
    "robot": "🤖", "book": "📚", "calendar": "📅", "support": "💬",
    "users": "👥", "profile": "👤", "admin": "⚙️", "fire": "🔥",
    "star": "⭐", "check": "✅", "cross": "❌", "back": "◀️",
    "home": "🏠", "plus": "➕", "edit": "✏️", "trash": "🗑",
    "megaphone": "📢", "clock": "🕐", "id": "🆔", "gem": "💎",
    "bell": "🔔", "sparkles": "✨", "write": "✍️", "folder": "📂",
    "info": "ℹ️", "warning": "⚠️", "rocket": "🚀", "crown": "👑",
}

def e(key: str) -> str:
    ch = EMOJI.get(key, "")
    if USE_PREMIUM and key in PREMIUM_IDS:
        return f'<tg-emoji emoji-id="{PREMIUM_IDS[key]}">{ch}</tg-emoji>'
    return ch

# ============ BOT ============
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN не задан!")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# ============ MIDDLEWARE: ловит ошибки и гарантирует ответ на callback ============
class SafetyMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery):
            log.info(f"CB from {event.from_user.id}: {event.data!r}")
        try:
            return await handler(event, data)
        except Exception as ex:
            log.error(f"HANDLER ERROR: {type(ex).__name__}: {ex}\n{traceback.format_exc()}")
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer("⚠️ Ошибка. Попробуй ещё раз.", show_alert=True)
                except Exception:
                    pass
            elif isinstance(event, Message):
                try:
                    await event.answer("⚠️ Ошибка. Попробуй ещё раз.")
                except Exception:
                    pass
            return None

dp.message.middleware(SafetyMiddleware())
dp.callback_query.middleware(SafetyMiddleware())

# ============ FSM ============
class ModelStates(StatesGroup):
    add_name = State()
    edit_name = State()

class HWStates(StatesGroup):
    add_date = State()
    add_month = State()
    add_week = State()
    add_content = State()

class SubjectStates(StatesGroup):
    add_name = State()

class GDZStates(StatesGroup):
    add_date = State()
    add_month = State()
    add_week = State()
    model_content = State()

class BroadcastStates(StatesGroup):
    text = State()

class UserMsgStates(StatesGroup):
    text = State()

# ============ DB ============
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            model_id INTEGER,
            registered_at TEXT
        );
        CREATE TABLE IF NOT EXISTS models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            is_recommended INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS homework (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_str TEXT, month_str TEXT, week_str TEXT,
            content_text TEXT, content_photo TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
        );
        CREATE TABLE IF NOT EXISTS gdz (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id INTEGER, model_id INTEGER,
            date_str TEXT, month_str TEXT, week_str TEXT,
            content_text TEXT, content_photo TEXT,
            created_at TEXT
        );
        """)
        await db.commit()

async def get_user(uid: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE tg_id=?", (uid,))
        return await cur.fetchone()

async def register_user(user):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (tg_id, username, full_name, registered_at) VALUES (?,?,?,?)",
            (user.id, user.username or "", user.full_name or "", datetime.now().isoformat()),
        )
        await db.execute(
            "UPDATE users SET username=?, full_name=? WHERE tg_id=?",
            (user.username or "", user.full_name or "", user.id),
        )
        await db.commit()

# ============ HELPERS ============
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

async def safe_edit(call: CallbackQuery, text: str, kb=None):
    try:
        await call.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as ex:
        if "message is not modified" in str(ex):
            return
        try:
            await call.message.answer(text, reply_markup=kb)
        except Exception:
            pass
    except Exception:
        try:
            await call.message.answer(text, reply_markup=kb)
        except Exception:
            pass

async def safe_send(chat_id: int, text: str, kb=None):
    try:
        await bot.send_message(chat_id, text, reply_markup=kb)
    except Exception as ex:
        log.warning(f"send failed to {chat_id}: {ex}")

def back_btn(data="main_menu"):
    return InlineKeyboardButton(text=f"{e('back')} Назад", callback_data=data)

# ============ MAIN MENU ============
def main_menu_kb(is_admin_flag=False, has_model=False):
    kb = [
        [InlineKeyboardButton(
            text=f"{e('robot')} {'Сменить модель ИИ' if has_model else 'Выбрать модель ИИ'}",
            callback_data="models_list")],
        [InlineKeyboardButton(text=f"{e('calendar')} ДЗ", callback_data="hw_menu"),
         InlineKeyboardButton(text=f"{e('book')} ГДЗ", callback_data="gdz_subjects")],
        [InlineKeyboardButton(text=f"{e('support')} Поддержка", callback_data="support")],
        [InlineKeyboardButton(text=f"{e('users')} Создатели", callback_data="creators"),
         InlineKeyboardButton(text=f"{e('profile')} Профиль", callback_data="profile")],
    ]
    if is_admin_flag:
        kb.append([InlineKeyboardButton(text=f"{e('admin')} Админ панель", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

async def show_main(target, edit=False):
    uid = target.from_user.id
    user = await get_user(uid)
    has_model = bool(user and user["model_id"])
    text = (f"{e('sparkles')} <b>Главное меню</b>\n\n"
            f"Привет, <b>{escape(target.from_user.full_name or 'друг')}</b>!\n"
            f"Выбери нужный раздел 👇")
    kb = main_menu_kb(is_admin(uid), has_model)
    if edit and isinstance(target, CallbackQuery):
        await safe_edit(target, text, kb)
    else:
        m = target.message if isinstance(target, CallbackQuery) else target
        await m.answer(text, reply_markup=kb)

# ============ START / HELP / TEST ============
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await register_user(message.from_user)
    await message.answer(
        f"{e('rocket')} <b>Добро пожаловать в ГДЗ Бот!</b>\n\n"
        f"Здесь ты найдёшь готовые домашние задания и расписание ДЗ.\n\n"
        f"{e('info')} Для начала выбери модель ИИ.",
        reply_markup=main_menu_kb(is_admin(message.from_user.id), False),
    )

@router.message(Command("test"))
async def cmd_test(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Проверить кнопки", callback_data="test_ping")]
    ])
    await message.answer("Тест кнопок:", reply_markup=kb)

@router.callback_query(F.data == "test_ping")
async def cb_test(call: CallbackQuery):
    await call.answer("✅ Кнопки работают!", show_alert=True)

@router.callback_query(F.data == "main_menu")
async def cb_main(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await show_main(call, edit=True)
    await call.answer()

# ============ MODELS (user) ============
@router.callback_query(F.data == "models_list")
async def cb_models_list(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM models ORDER BY is_recommended DESC, name")
        models = await cur.fetchall()
    if not models:
        await safe_edit(call, f"{e('warning')} Пока нет доступных моделей ИИ.",
                        InlineKeyboardMarkup(inline_keyboard=[[back_btn()]]))
        await call.answer(); return
    kb = []
    for m in models:
        rec = f"  {e('star')} Рекомендуем" if m["is_recommended"] else ""
        kb.append([InlineKeyboardButton(text=f"{e('robot')} {m['name']}{rec}",
                                        callback_data=f"pick_model_{m['id']}")])
    kb.append([back_btn()])
    await safe_edit(call, f"{e('robot')} <b>Выбери модель ИИ:</b>",
                    InlineKeyboardMarkup(inline_keyboard=kb))
    await call.answer()

@router.callback_query(F.data.startswith("pick_model_"))
async def cb_pick_model(call: CallbackQuery):
    mid = int(call.data.split("_")[-1])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM models WHERE id=?", (mid,))
        m = await cur.fetchone()
        if not m:
            await call.answer("Модель не найдена", show_alert=True); return
        await db.execute("UPDATE users SET model_id=? WHERE tg_id=?", (mid, call.from_user.id))
        await db.commit()
    await call.answer(f"Модель {m['name']} выбрана ✅", show_alert=True)
    await show_main(call, edit=True)

# ============ ДЗ (user) ============
@router.callback_query(F.data == "hw_menu")
async def cb_hw_menu(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id, date_str, month_str, week_str FROM homework ORDER BY id DESC")
        rows = await cur.fetchall()
    if not rows:
        await safe_edit(call, f"{e('warning')} Пока нет расписания ДЗ.",
                        InlineKeyboardMarkup(inline_keyboard=[[back_btn()]]))
        await call.answer(); return
    kb = [[InlineKeyboardButton(
        text=f"{e('calendar')} {r['date_str']} · {r['month_str']} · {r['week_str']}",
        callback_data=f"hw_view_{r['id']}")] for r in rows]
    kb.append([back_btn()])
    await safe_edit(call, f"{e('calendar')} <b>Выбери дату ДЗ:</b>",
                    InlineKeyboardMarkup(inline_keyboard=kb))
    await call.answer()

@router.callback_query(F.data.startswith("hw_view_"))
async def cb_hw_view(call: CallbackQuery):
    hid = int(call.data.split("_")[-1])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM homework WHERE id=?", (hid,))
        hw = await cur.fetchone()
    if not hw:
        await call.answer("Не найдено", show_alert=True); return
    header = f"{e('calendar')} <b>ДЗ на {hw['date_str']} · {hw['month_str']} · {hw['week_str']}</b>\n\n"
    kb = InlineKeyboardMarkup(inline_keyboard=[[back_btn("hw_menu")]])
    if hw["content_photo"]:
        try:
            await call.message.answer_photo(hw["content_photo"],
                caption=(hw["content_text"] or header), reply_markup=kb)
            try: await call.message.delete()
            except Exception: pass
        except Exception:
            await call.message.answer(header + (hw["content_text"] or ""), reply_markup=kb)
    else:
        await safe_edit(call, header + (hw["content_text"] or "—"), kb)
    await call.answer()

# ============ ГДЗ (user) ============
@router.callback_query(F.data == "gdz_subjects")
async def cb_gdz_subjects(call: CallbackQuery):
    user = await get_user(call.from_user.id)
    if not user or not user["model_id"]:
        await safe_edit(call, f"{e('warning')} Сначала выбери модель ИИ!",
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"{e('robot')} Выбрать модель", callback_data="models_list")],
                [back_btn()]]))
        await call.answer(); return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM subjects ORDER BY name")
        subjects = await cur.fetchall()
    if not subjects:
        await safe_edit(call, f"{e('warning')} Пока нет предметов.",
                        InlineKeyboardMarkup(inline_keyboard=[[back_btn()]]))
        await call.answer(); return
    kb = [[InlineKeyboardButton(text=f"{e('book')} {s['name']}",
                                callback_data=f"gdz_subj_{s['id']}")] for s in subjects]
    kb.append([back_btn()])
    await safe_edit(call, f"{e('book')} <b>Выбери предмет:</b>",
                    InlineKeyboardMarkup(inline_keyboard=kb))
    await call.answer()

@router.callback_query(F.data.startswith("gdz_subj_"))
async def cb_gdz_subject(call: CallbackQuery):
    sid = int(call.data.split("_")[-1])
    user = await get_user(call.from_user.id)
    if not user or not user["model_id"]:
        await call.answer("Сначала выбери модель ИИ!", show_alert=True); return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM subjects WHERE id=?", (sid,))
        subj = await cur.fetchone()
        cur = await db.execute(
            "SELECT id, date_str, month_str, week_str FROM gdz "
            "WHERE subject_id=? AND model_id=? ORDER BY id DESC",
            (sid, user["model_id"]))
        rows = await cur.fetchall()
    if not subj:
        await call.answer("Не найдено", show_alert=True); return
    if not rows:
        await safe_edit(call,
            f"{e('warning')} По предмету <b>{subj['name']}</b> пока нет ГДЗ для выбранной модели.",
            InlineKeyboardMarkup(inline_keyboard=[[back_btn("gdz_subjects")]]))
        await call.answer(); return
    kb = [[InlineKeyboardButton(
        text=f"{e('calendar')} {r['date_str']} · {r['month_str']} · {r['week_str']}",
        callback_data=f"gdz_view_{r['id']}")] for r in rows]
    kb.append([back_btn("gdz_subjects")])
    await safe_edit(call, f"{e('book')} <b>{subj['name']}</b>\nВыбери ГДЗ:",
                    InlineKeyboardMarkup(inline_keyboard=kb))
    await call.answer()

@router.callback_query(F.data.startswith("gdz_view_"))
async def cb_gdz_view(call: CallbackQuery):
    gid = int(call.data.split("_")[-1])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM gdz WHERE id=?", (gid,))
        g = await cur.fetchone()
    if not g:
        await call.answer("Не найдено", show_alert=True); return
    header = (f"{e('book')} <b>ГДЗ</b>\n"
              f"{e('calendar')} {g['date_str']} · {g['month_str']} · {g['week_str']}\n\n")
    kb = InlineKeyboardMarkup(inline_keyboard=[[back_btn(f"gdz_subj_{g['subject_id']}")]])
    if g["content_photo"]:
        try:
            await call.message.answer_photo(g["content_photo"],
                caption=header + (g["content_text"] or ""), reply_markup=kb)
            try: await call.message.delete()
            except Exception: pass
        except Exception:
            await call.message.answer(header + (g["content_text"] or ""), reply_markup=kb)
    else:
        await safe_edit(call, header + (g["content_text"] or "—"), kb)
    await call.answer()

# ============ SUPPORT / CREATORS / PROFILE ============
@router.callback_query(F.data == "support")
async def cb_support(call: CallbackQuery):
    user = call.from_user
    await safe_edit(call,
        f"{e('support')} <b>Поддержка</b>\n\n"
        f"Если у тебя возникли вопросы — напиши:\n"
        f"👉 {SUPPORT_CONTACT}",
        InlineKeyboardMarkup(inline_keyboard=[[back_btn()]]))
    name = escape(user.full_name or "—")
    uname = f"@{user.username}" if user.username else name
    note = (f"{e('bell')} <b>Пользователь в разделе Поддержка</b>\n\n"
            f"{e('profile')} {name}\n"
            f"{e('id')} <code>{user.id}</code>\n"
            f"{e('info')} {uname}")
    targets = set(ADMIN_IDS)
    if OWNER_ID:
        targets.add(OWNER_ID)
    for aid in targets:
        if aid:
            await safe_send(aid, note)
    await call.answer()

@router.callback_query(F.data == "creators")
async def cb_creators(call: CallbackQuery):
    text = f"{e('crown')} <b>Создатели</b>\n\n"
    for c in CREATORS:
        text += f"• {c}\n"
    text += f"\n{e('sparkles')} Спасибо, что пользуешься ботом!"
    await safe_edit(call, text, InlineKeyboardMarkup(inline_keyboard=[[back_btn()]]))
    await call.answer()

@router.callback_query(F.data == "profile")
async def cb_profile(call: CallbackQuery):
    u = call.from_user
    user = await get_user(u.id)
    model_name = "не выбрана"
    if user and user["model_id"]:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT name FROM models WHERE id=?", (user["model_id"],))
            m = await cur.fetchone()
            if m:
                model_name = m["name"]
    reg = user["registered_at"][:19] if user and user["registered_at"] else "—"
    text = (f"{e('profile')} <b>Профиль</b>\n\n"
            f"{e('id')} ID: <code>{u.id}</code>\n"
            f"{e('write')} Имя: <b>{escape(u.full_name or '—')}</b>\n"
            f"{e('users')} Юзернейм: {'@' + u.username if u.username else '—'}\n"
            f"{e('robot')} Модель ИИ: <b>{model_name}</b>\n"
            f"{e('clock')} Регистрация: {reg}\n"
            f"{e('crown')} Статус: {'Админ' if is_admin(u.id) else 'Пользователь'}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{e('robot')} Сменить модель", callback_data="models_list")],
        [back_btn()]])
    await safe_edit(call, text, kb)
    await call.answer()

# ============ ADMIN PANEL ============
def admin_menu_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{e('users')} Пользователи", callback_data="adm_users_0"),
         InlineKeyboardButton(text=f"{e('megaphone')} Рассылка", callback_data="adm_broadcast")],
        [InlineKeyboardButton(text=f"{e('robot')} Модели", callback_data="adm_models"),
         InlineKeyboardButton(text=f"{e('calendar')} Расписание ДЗ", callback_data="adm_hw")],
        [InlineKeyboardButton(text=f"{e('book')} Предметы ГДЗ", callback_data="adm_subjects"),
         InlineKeyboardButton(text=f"{e('folder')} Контент ГДЗ", callback_data="adm_gdz_subjects")],
        [InlineKeyboardButton(text=f"{e('info')} Статистика", callback_data="adm_stats")],
        [back_btn()]])

@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа"); return
    await state.clear()
    await message.answer(f"{e('admin')} <b>Админ панель</b>", reply_markup=admin_menu_kb())

@router.callback_query(F.data == "admin")
async def cb_admin(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    await state.clear()
    await safe_edit(call, f"{e('admin')} <b>Админ панель</b>\n\nВыбери раздел:", admin_menu_kb())
    await call.answer()

@router.callback_query(F.data == "adm_stats")
async def cb_stats(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async def c(q):
            cur = await db.execute(q); return (await cur.fetchone())["c"]
        users = await c("SELECT COUNT(*) c FROM users")
        models = await c("SELECT COUNT(*) c FROM models")
        subjects = await c("SELECT COUNT(*) c FROM subjects")
        hw = await c("SELECT COUNT(*) c FROM homework")
        gdz = await c("SELECT COUNT(*) c FROM gdz")
    text = (f"{e('info')} <b>Статистика</b>\n\n"
            f"{e('users')} Пользователей: <b>{users}</b>\n"
            f"{e('robot')} Моделей: <b>{models}</b>\n"
            f"{e('book')} Предметов: <b>{subjects}</b>\n"
            f"{e('calendar')} Записей ДЗ: <b>{hw}</b>\n"
            f"{e('folder')} Записей ГДЗ: <b>{gdz}</b>")
    await safe_edit(call, text, InlineKeyboardMarkup(inline_keyboard=[[back_btn("admin")]]))
    await call.answer()

# ============ ADMIN: USERS ============
USERS_PER_PAGE = 10

@router.callback_query(F.data.startswith("adm_users_"))
async def cb_users(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    page = int(call.data.split("_")[-1])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT COUNT(*) c FROM users")
        total = (await cur.fetchone())["c"]
        cur = await db.execute(
            "SELECT * FROM users ORDER BY registered_at DESC LIMIT ? OFFSET ?",
            (USERS_PER_PAGE, page * USERS_PER_PAGE))
        users = await cur.fetchall()
    kb = []
    for u in users:
        label = f"{u['full_name'] or 'User'} ({'@'+u['username'] if u['username'] else u['tg_id']})"
        if len(label) > 45:
            label = label[:42] + "..."
        kb.append([InlineKeyboardButton(text=label, callback_data=f"adm_user_{u['tg_id']}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"adm_users_{page-1}"))
    pages_total = max(1, (total - 1) // USERS_PER_PAGE + 1)
    nav.append(InlineKeyboardButton(text=f"{page+1}/{pages_total}", callback_data="noop"))
    if (page + 1) * USERS_PER_PAGE < total:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"adm_users_{page+1}"))
    if nav:
        kb.append(nav)
    kb.append([back_btn("admin")])
    await safe_edit(call, f"{e('users')} <b>Пользователи</b> (всего: {total})",
                    InlineKeyboardMarkup(inline_keyboard=kb))
    await call.answer()

@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery):
    await call.answer()

@router.callback_query(F.data.startswith("adm_user_"))
async def cb_user_view(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    uid = int(call.data.split("_")[-1])
    user = await get_user(uid)
    if not user:
        await call.answer("Не найден", show_alert=True); return
    model_name = "—"
    if user["model_id"]:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT name FROM models WHERE id=?", (user["model_id"],))
            m = await cur.fetchone()
            if m:
                model_name = m["name"]
    text = (f"{e('profile')} <b>Профиль пользователя</b>\n\n"
            f"{e('id')} ID: <code>{user['tg_id']}</code>\n"
            f"{e('write')} Имя: {escape(user['full_name'] or '—')}\n"
            f"{e('users')} Юзернейм: {'@'+user['username'] if user['username'] else '—'}\n"
            f"{e('robot')} Модель: {model_name}\n"
            f"{e('clock')} Регистрация: {user['registered_at'][:19] if user['registered_at'] else '—'}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{e('write')} Написать сообщение", callback_data=f"adm_msg_{uid}")],
        [back_btn("adm_users_0")]])
    await safe_edit(call, text, kb)
    await call.answer()

@router.callback_query(F.data.startswith("adm_msg_"))
async def cb_user_msg(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    uid = int(call.data.split("_")[-1])
    await state.set_state(UserMsgStates.text)
    await state.update_data(target=uid)
    await safe_edit(call, f"{e('write')} Введи текст сообщения для <code>{uid}</code>:",
                    InlineKeyboardMarkup(inline_keyboard=[[back_btn(f"adm_user_{uid}")]]))
    await call.answer()

@router.message(UserMsgStates.text)
async def msg_to_user(message: Message, state: FSMContext):
    data = await state.get_data()
    uid = data.get("target")
    await state.clear()
    if not message.text:
        await message.answer("Нужен текст."); return
    try:
        await bot.send_message(uid, f"{e('bell')} <b>Сообщение от админа:</b>\n\n{message.text}")
        await message.answer(f"{e('check')} Отправлено.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_btn(f"adm_user_{uid}")]]))
    except Exception as ex:
        await message.answer(f"{e('cross')} Не удалось: {ex}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_btn(f"adm_user_{uid}")]]))

# ============ ADMIN: BROADCAST ============
@router.callback_query(F.data == "adm_broadcast")
async def cb_broadcast(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    await state.set_state(BroadcastStates.text)
    await safe_edit(call, f"{e('megaphone')} <b>Рассылка</b>\n\nОтправь текст для всех пользователей:",
                    InlineKeyboardMarkup(inline_keyboard=[[back_btn("admin")]]))
    await call.answer()

@router.message(BroadcastStates.text)
async def do_broadcast(message: Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id):
        return
    if not message.text:
        await message.answer("Нужен текст."); return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT tg_id FROM users")
        users = await cur.fetchall()
    msg = await message.answer(f"{e('clock')} Отправка... 0/{len(users)}")
    ok = 0
    for i, u in enumerate(users):
        try:
            await bot.send_message(u["tg_id"], f"{e('megaphone')} {message.text}")
            ok += 1
        except Exception:
            pass
        if (i + 1) % 25 == 0:
            try:
                await msg.edit_text(f"{e('clock')} Отправка... {i+1}/{len(users)}")
            except Exception:
                pass
    await msg.edit_text(f"{e('check')} <b>Готово</b>\nОтправлено: {ok}/{len(users)}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_btn("admin")]]))

# ============ ADMIN: MODELS ============
async def render_adm_models(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM models ORDER BY is_recommended DESC, name")
        models = await cur.fetchall()
    kb = []
    for m in models:
        rec = " ⭐" if m["is_recommended"] else ""
        kb.append([InlineKeyboardButton(text=f"🤖 {m['name']}{rec}",
                                        callback_data=f"adm_model_{m['id']}")])
    kb.append([InlineKeyboardButton(text=f"{e('plus')} Добавить модель", callback_data="adm_model_add")])
    kb.append([back_btn("admin")])
    await safe_edit(call, f"{e('robot')} <b>Модели ИИ</b>",
                    InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data == "adm_models")
async def cb_adm_models(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    await state.clear()
    await render_adm_models(call)
    await call.answer()

@router.callback_query(F.data == "adm_model_add")
async def cb_model_add(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    await state.set_state(ModelStates.add_name)
    await safe_edit(call, f"{e('plus')} Введи название модели:",
                    InlineKeyboardMarkup(inline_keyboard=[[back_btn("adm_models")]]))
    await call.answer()

@router.message(ModelStates.add_name)
async def model_add_name(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Отправь текст."); return
    name = message.text.strip()
    if not name:
        await message.answer("Пустое имя."); return
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO models (name) VALUES (?)", (name,))
            await db.commit()
    except Exception:
        await message.answer(f"{e('cross')} Такая модель уже есть."); return
    await state.clear()
    await message.answer(f"{e('check')} Модель <b>{escape(name)}</b> добавлена.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_btn("adm_models")]]))

@router.callback_query(F.data.startswith("adm_model_rename_"))
async def cb_model_rename(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    mid = int(call.data.split("_")[-1])
    await state.set_state(ModelStates.edit_name)
    await state.update_data(mid=mid)
    await safe_edit(call, f"{e('edit')} Введи новое название модели:",
                    InlineKeyboardMarkup(inline_keyboard=[[back_btn(f"adm_model_{mid}")]]))
    await call.answer()

@router.message(ModelStates.edit_name)
async def model_rename(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Отправь текст."); return
    data = await state.get_data()
    mid = data.get("mid")
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE models SET name=? WHERE id=?", (message.text.strip(), mid))
            await db.commit()
    except Exception:
        await message.answer("Ошибка: имя занято."); return
    await state.clear()
    await message.answer(f"{e('check')} Переименовано.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_btn(f"adm_model_{mid}")]]))

@router.callback_query(F.data.startswith("adm_model_rec_"))
async def cb_model_rec(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    mid = int(call.data.split("_")[-1])
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT is_recommended FROM models WHERE id=?", (mid,))
        r = await cur.fetchone()
        if r:
            new_val = 0 if r[0] else 1
            await db.execute("UPDATE models SET is_recommended=? WHERE id=?", (new_val, mid))
            await db.commit()
    await cb_model_view(call, answer=False)

@router.callback_query(F.data.startswith("adm_model_del_"))
async def cb_model_del(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    mid = int(call.data.split("_")[-1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM models WHERE id=?", (mid,))
        await db.execute("UPDATE users SET model_id=NULL WHERE model_id=?", (mid,))
        await db.commit()
    await render_adm_models(call)
    await call.answer("Удалено", show_alert=True)

@router.callback_query(F.data.startswith("adm_model_"))
async def cb_model_view(call: CallbackQuery, answer: bool = True):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    if call.data == "adm_model_add":
        return
    mid = int(call.data.split("_")[-1])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM models WHERE id=?", (mid,))
        m = await cur.fetchone()
    if not m:
        if answer:
            await call.answer("Не найдено", show_alert=True)
        return
    rec_status = "включена" if m["is_recommended"] else "выключена"
    text = f"{e('robot')} <b>{m['name']}</b>\n\n{e('star')} Рекомендуем: {rec_status}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{e('edit')} Переименовать", callback_data=f"adm_model_rename_{mid}"),
         InlineKeyboardButton(text=f"{e('star')} Рекоменд.", callback_data=f"adm_model_rec_{mid}")],
        [InlineKeyboardButton(text=f"{e('trash')} Удалить", callback_data=f"adm_model_del_{mid}")],
        [back_btn("adm_models")]])
    await safe_edit(call, text, kb)
    if answer:
        await call.answer()

# ============ ADMIN: HOMEWORK ============
async def render_adm_hw(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM homework ORDER BY id DESC")
        rows = await cur.fetchall()
    kb = []
    for r in rows:
        label = f"{r['date_str']} · {r['month_str']} · {r['week_str']}"
        kb.append([InlineKeyboardButton(text=f"📅 {label}", callback_data=f"adm_hw_{r['id']}")])
    kb.append([InlineKeyboardButton(text=f"{e('plus')} Добавить ДЗ", callback_data="adm_hw_add")])
    kb.append([back_btn("admin")])
    await safe_edit(call, f"{e('calendar')} <b>Расписание ДЗ</b>",
                    InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data == "adm_hw")
async def cb_adm_hw(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    await state.clear()
    await render_adm_hw(call)
    await call.answer()

@router.callback_query(F.data == "adm_hw_add")
async def cb_hw_add(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    await state.set_state(HWStates.add_date)
    await safe_edit(call, f"{e('calendar')} Введи дату (например: 12.05.2025):",
                    InlineKeyboardMarkup(inline_keyboard=[[back_btn("adm_hw")]]))
    await call.answer()

@router.message(HWStates.add_date)
async def hw_add_date(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Отправь текст."); return
    await state.update_data(date_str=message.text.strip())
    await state.set_state(HWStates.add_month)
    await message.answer(f"{e('calendar')} Введи месяц:")

@router.message(HWStates.add_month)
async def hw_add_month(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Отправь текст."); return
    await state.update_data(month_str=message.text.strip())
    await state.set_state(HWStates.add_week)
    await message.answer(f"{e('calendar')} Введи неделю:")

@router.message(HWStates.add_week)
async def hw_add_week(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Отправь текст."); return
    await state.update_data(week_str=message.text.strip())
    await state.set_state(HWStates.add_content)
    await message.answer(f"{e('write')} Отправь текст или фото (с подписью) для этого ДЗ:")

@router.message(HWStates.add_content)
async def hw_add_content(message: Message, state: FSMContext):
    data = await state.get_data()
    text = None; photo = None
    if message.photo:
        photo = message.photo[-1].file_id
        text = message.caption
    else:
        text = message.text
    if not text and not photo:
        await message.answer("Отправь текст или фото."); return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO homework (date_str, month_str, week_str, content_text, content_photo, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (data["date_str"], data["month_str"], data["week_str"], text, photo, datetime.now().isoformat()))
        await db.commit()
    await state.clear()
    await message.answer(f"{e('check')} ДЗ добавлено.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_btn("adm_hw")]]))

@router.callback_query(F.data.startswith("adm_hw_del_"))
async def cb_hw_del(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    hid = int(call.data.split("_")[-1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM homework WHERE id=?", (hid,))
        await db.commit()
    await render_adm_hw(call)
    await call.answer("Удалено", show_alert=True)

@router.callback_query(F.data.startswith("adm_hw_"))
async def cb_hw_view_adm(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    if call.data == "adm_hw_add":
        return
    hid = int(call.data.split("_")[-1])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM homework WHERE id=?", (hid,))
        hw = await cur.fetchone()
    if not hw:
        await call.answer("Не найдено", show_alert=True); return
    text = (f"{e('calendar')} <b>ДЗ</b>\n"
            f"Дата: {hw['date_str']}\nМесяц: {hw['month_str']}\nНеделя: {hw['week_str']}\n\n"
            f"Текст: {hw['content_text'] or '—'}\n"
            f"Фото: {'есть' if hw['content_photo'] else 'нет'}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{e('trash')} Удалить", callback_data=f"adm_hw_del_{hid}")],
        [back_btn("adm_hw")]])
    await safe_edit(call, text, kb)
    await call.answer()

# ============ ADMIN: SUBJECTS ============
async def render_adm_subjects(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM subjects ORDER BY name")
        subs = await cur.fetchall()
    kb = [[InlineKeyboardButton(text=f"📚 {s['name']}", callback_data=f"adm_subj_{s['id']}")] for s in subs]
    kb.append([InlineKeyboardButton(text=f"{e('plus')} Добавить предмет", callback_data="adm_subj_add")])
    kb.append([back_btn("admin")])
    await safe_edit(call, f"{e('book')} <b>Предметы</b>",
                    InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data == "adm_subjects")
async def cb_adm_subjects(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    await state.clear()
    await render_adm_subjects(call)
    await call.answer()

@router.callback_query(F.data == "adm_subj_add")
async def cb_subj_add(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    await state.set_state(SubjectStates.add_name)
    await safe_edit(call, f"{e('plus')} Введи название предмета:",
                    InlineKeyboardMarkup(inline_keyboard=[[back_btn("adm_subjects")]]))
    await call.answer()

@router.message(SubjectStates.add_name)
async def subj_add(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Отправь текст."); return
    name = message.text.strip()
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO subjects (name) VALUES (?)", (name,))
            await db.commit()
    except Exception:
        await message.answer("Предмет уже существует."); return
    await state.clear()
    await message.answer(f"{e('check')} Предмет добавлен.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_btn("adm_subjects")]]))

@router.callback_query(F.data.startswith("adm_subj_del_"))
async def cb_subj_del(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    sid = int(call.data.split("_")[-1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM subjects WHERE id=?", (sid,))
        await db.execute("DELETE FROM gdz WHERE subject_id=?", (sid,))
        await db.commit()
    await render_adm_subjects(call)
    await call.answer("Удалено", show_alert=True)

@router.callback_query(F.data.startswith("adm_subj_"))
async def cb_subj_view(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    if call.data == "adm_subj_add":
        return
    sid = int(call.data.split("_")[-1])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM subjects WHERE id=?", (sid,))
        s = await cur.fetchone()
    if not s:
        await call.answer("Не найдено", show_alert=True); return
    text = f"{e('book')} <b>{s['name']}</b>"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{e('folder')} ГДЗ по предмету", callback_data=f"adm_gdz_list_{sid}")],
        [InlineKeyboardButton(text=f"{e('trash')} Удалить", callback_data=f"adm_subj_del_{sid}")],
        [back_btn("adm_subjects")]])
    await safe_edit(call, text, kb)
    await call.answer()

# ============ ADMIN: GDZ CONTENT ============
async def render_adm_gdz_list(call: CallbackQuery, sid: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM subjects WHERE id=?", (sid,))
        s = await cur.fetchone()
        cur = await db.execute(
            "SELECT date_str, month_str, week_str, MIN(id) as mid, COUNT(*) as cnt FROM gdz "
            "WHERE subject_id=? GROUP BY date_str, month_str, week_str ORDER BY mid DESC",
            (sid,))
        groups = await cur.fetchall()
    kb = []
    for g in groups:
        label = f"{g['date_str']} · {g['month_str']} · {g['week_str']} ({g['cnt']})"
        kb.append([InlineKeyboardButton(text=f"📖 {label}", callback_data=f"adm_gdz_group_{g['mid']}")])
    kb.append([InlineKeyboardButton(text=f"{e('plus')} Добавить ГДЗ", callback_data=f"adm_gdz_add_{sid}")])
    kb.append([back_btn("adm_gdz_subjects")])
    await safe_edit(call, f"{e('folder')} <b>{s['name']}</b>\n\nВсе ГДЗ по предмету:",
                    InlineKeyboardMarkup(inline_keyboard=kb))

@router.callback_query(F.data == "adm_gdz_subjects")
async def cb_adm_gdz_subjects(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM subjects ORDER BY name")
        subs = await cur.fetchall()
    kb = [[InlineKeyboardButton(text=f"📚 {s['name']}", callback_data=f"adm_gdz_list_{s['id']}")] for s in subs]
    kb.append([back_btn("admin")])
    await safe_edit(call, f"{e('folder')} <b>Выбери предмет:</b>",
                    InlineKeyboardMarkup(inline_keyboard=kb))
    await call.answer()

@router.callback_query(F.data.startswith("adm_gdz_list_"))
async def cb_adm_gdz_list(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    await state.clear()
    sid = int(call.data.split("_")[-1])
    await render_adm_gdz_list(call, sid)
    await call.answer()

@router.callback_query(F.data.startswith("adm_gdz_add_"))
async def cb_gdz_add(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    sid = int(call.data.split("_")[-1])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT COUNT(*) c FROM models")
        cnt = (await cur.fetchone())["c"]
    if cnt == 0:
        await call.answer("Сначала добавь хотя бы одну модель ИИ!", show_alert=True); return
    await state.set_state(GDZStates.add_date)
    await state.update_data(sid=sid)
    await safe_edit(call, f"{e('calendar')} Введи дату (например: 12.05.2025):",
                    InlineKeyboardMarkup(inline_keyboard=[[back_btn(f"adm_gdz_list_{sid}")]]))
    await call.answer()

@router.message(GDZStates.add_date)
async def gdz_add_date(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Отправь текст."); return
    await state.update_data(date_str=message.text.strip())
    await state.set_state(GDZStates.add_month)
    await message.answer(f"{e('calendar')} Введи месяц:")

@router.message(GDZStates.add_month)
async def gdz_add_month(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Отправь текст."); return
    await state.update_data(month_str=message.text.strip())
    await state.set_state(GDZStates.add_week)
    await message.answer(f"{e('calendar')} Введи неделю:")

@router.message(GDZStates.add_week)
async def gdz_add_week(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Отправь текст."); return
    await state.update_data(week_str=message.text.strip())
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM models ORDER BY is_recommended DESC, name")
        models = await cur.fetchall()
    models_list = [dict(m) for m in models]
    await state.update_data(models=models_list, idx=0, collected=[])
    await state.set_state(GDZStates.model_content)
    first = models_list[0]
    await message.answer(
        f"{e('info')} <b>Важно:</b> контент нужно загрузить для <b>каждой</b> модели ИИ.\n\n"
        f"{e('robot')} Модель <b>1/{len(models_list)}</b>: <b>{first['name']}</b>\n\n"
        f"Отправь текст или фото (с подписью) для этой модели:")

@router.message(GDZStates.model_content)
async def gdz_model_content(message: Message, state: FSMContext):
    data = await state.get_data()
    models = data["models"]; idx = data["idx"]; collected = data["collected"]
    text = None; photo = None
    if message.photo:
        photo = message.photo[-1].file_id
        text = message.caption
    else:
        text = message.text
    if not text and not photo:
        await message.answer("Отправь текст или фото."); return
    collected.append({"model_id": models[idx]["id"], "text": text, "photo": photo})
    idx += 1
    if idx >= len(models):
        async with aiosqlite.connect(DB_PATH) as db:
            for it in collected:
                await db.execute(
                    "INSERT INTO gdz (subject_id, model_id, date_str, month_str, week_str, "
                    "content_text, content_photo, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (data["sid"], it["model_id"], data["date_str"], data["month_str"],
                     data["week_str"], it["text"], it["photo"], datetime.now().isoformat()))
            await db.commit()
        await state.clear()
        await message.answer(f"{e('check')} ГДЗ добавлено для всех моделей!",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_btn(f"adm_gdz_list_{data['sid']}")]]))
    else:
        await state.update_data(idx=idx, collected=collected)
        next_m = models[idx]
        await message.answer(
            f"{e('robot')} Модель <b>{idx+1}/{len(models)}</b>: <b>{next_m['name']}</b>\n\n"
            f"Отправь текст или фото:")

@router.callback_query(F.data.startswith("adm_gdz_delgrp_"))
async def cb_gdz_delgrp(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    gid = int(call.data.split("_")[-1])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM gdz WHERE id=?", (gid,))
        base = await cur.fetchone()
        if base:
            await db.execute(
                "DELETE FROM gdz WHERE subject_id=? AND date_str=? AND month_str=? AND week_str=?",
                (base["subject_id"], base["date_str"], base["month_str"], base["week_str"]))
            await db.commit()
    await call.answer("Удалено", show_alert=True)
    if base:
        await render_adm_gdz_list(call, base["subject_id"])

@router.callback_query(F.data.startswith("adm_gdz_del_"))
async def cb_gdz_del(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    gid = int(call.data.split("_")[-1])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT subject_id FROM gdz WHERE id=?", (gid,))
        r = await cur.fetchone()
        await db.execute("DELETE FROM gdz WHERE id=?", (gid,))
        await db.commit()
    await call.answer("Удалено", show_alert=True)
    if r:
        await render_adm_gdz_list(call, r["subject_id"])

@router.callback_query(F.data.startswith("adm_gdz_group_"))
async def cb_gdz_group(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    gid = int(call.data.split("_")[-1])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM gdz WHERE id=?", (gid,))
        base = await cur.fetchone()
    if not base:
        await call.answer("Не найдено", show_alert=True); return
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT g.*, m.name as model_name FROM gdz g "
            "LEFT JOIN models m ON g.model_id=m.id "
            "WHERE g.subject_id=? AND g.date_str=? AND g.month_str=? AND g.week_str=? "
            "ORDER BY m.name",
            (base["subject_id"], base["date_str"], base["month_str"], base["week_str"]))
        items = await cur.fetchall()
    kb = []
    for it in items:
        has = "📄" if it["content_photo"] else "📝"
        kb.append([InlineKeyboardButton(text=f"{has} {it['model_name']}",
                                        callback_data=f"adm_gdz_item_{it['id']}")])
    kb.append([InlineKeyboardButton(text=f"{e('trash')} Удалить всё ГДЗ",
                                    callback_data=f"adm_gdz_delgrp_{gid}")])
    kb.append([back_btn(f"adm_gdz_list_{base['subject_id']}")])
    text = (f"{e('folder')} <b>ГДЗ группа</b>\n"
            f"{e('calendar')} {base['date_str']} · {base['month_str']} · {base['week_str']}\n\n"
            f"Выбери модель:")
    await safe_edit(call, text, InlineKeyboardMarkup(inline_keyboard=kb))
    await call.answer()

@router.callback_query(F.data.startswith("adm_gdz_item_"))
async def cb_gdz_item(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True); return
    gid = int(call.data.split("_")[-1])
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT g.*, m.name as model_name FROM gdz g "
            "LEFT JOIN models m ON g.model_id=m.id WHERE g.id=?", (gid,))
        it = await cur.fetchone()
    if not it:
        await call.answer("Не найдено", show_alert=True); return
    text = (f"{e('robot')} <b>{it['model_name']}</b>\n"
            f"{e('calendar')} {it['date_str']} · {it['month_str']} · {it['week_str']}\n\n"
            f"Текст: {it['content_text'] or '—'}\n"
            f"Фото: {'есть' if it['content_photo'] else 'нет'}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{e('trash')} Удалить", callback_data=f"adm_gdz_del_{gid}")],
        [back_btn(f"adm_gdz_list_{it['subject_id']}")]])
    await safe_edit(call, text, kb)
    await call.answer()

# ============ FALLBACK ============
@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer("Используй /start для открытия меню.")

@router.message()
async def fallback(message: Message, state: FSMContext):
    if await state.get_state():
        return
    await register_user(message.from_user)
    await message.answer("Используй кнопки меню 👇",
                         reply_markup=main_menu_kb(is_admin(message.from_user.id), False))

# ============ RUN ============
async def main():
    # Убираем вебхук и сбрасываем накопившиеся обновления
    await bot.delete_webhook(drop_pending_updates=True)
    await init_db()
    log.info("Bot started (polling)")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
