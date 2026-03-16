import asyncio
import logging
import os
import re
import ssl
import urllib.parse
from datetime import datetime
from functools import wraps
from logging.handlers import RotatingFileHandler
from typing import Optional

import aiohttp
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv


# ── Logging ───────────────────────────────────────────────────────────────────

def _setup_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        handlers=[
            RotatingFileHandler(
                "bot.log",
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            ),
            logging.StreamHandler(),
        ],
    )
    return logging.getLogger(__name__)


logger = _setup_logging()


# ── Config ────────────────────────────────────────────────────────────────────

load_dotenv()


def _require(name: str) -> str:
    val = os.getenv(name, "").strip()
    if not val:
        raise EnvironmentError(f"Переменная окружения {name!r} не задана")
    return val


class Config:
    BOT_TOKEN: str = _require("BOT_TOKEN")
    OUTLINE_URL: str = _require("OUTLINE_API_URL").rstrip("/")
    ADMINS: list[int] = [
        int(x.strip())
        for x in _require("ALLOWED_IDS").split(",")
        if x.strip().isdigit()
    ]
    CERT_FILE: str = "outline_cert.pem"
    # Префикс для автоимени при быстром создании ключа. Меняется через бот (до перезапуска).
    KEY_PREFIX: str = os.getenv("KEY_PREFIX", "Key").strip() or "Key"


# ── Outline API ───────────────────────────────────────────────────────────────

class OutlineAPI:
    _session: Optional[aiohttp.ClientSession] = None

    @classmethod
    def _make_ssl(cls) -> ssl.SSLContext | bool:
        if os.path.exists(Config.CERT_FILE):
            ctx = ssl.create_default_context(cafile=Config.CERT_FILE)
            # Outline использует certificate pinning без привязки к hostname/IP.
            ctx.check_hostname = False
            logger.info("SSL: используется %s", Config.CERT_FILE)
            return ctx
        logger.warning("outline_cert.pem не найден — SSL-верификация ОТКЛЮЧЕНА")
        return False

    @classmethod
    async def start(cls) -> None:
        cls._session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=cls._make_ssl()),
            timeout=aiohttp.ClientTimeout(total=10),
        )

    @classmethod
    async def stop(cls) -> None:
        if cls._session:
            await cls._session.close()
            cls._session = None

    @classmethod
    async def _req(
        cls, method: str, endpoint: str, **kwargs
    ) -> Optional[dict | bool]:
        if not cls._session:
            raise RuntimeError("Сессия не инициализирована — вызовите OutlineAPI.start()")
        url = f"{Config.OUTLINE_URL}/{endpoint}"
        try:
            async with cls._session.request(method, url, **kwargs) as r:
                logger.debug("%s %s → %d", method, url, r.status)
                if r.status == 204:
                    return True
                if r.status in (200, 201):
                    return await r.json()
                logger.error("API %d: %s", r.status, await r.text())
                return None
        except aiohttp.ClientError as e:
            logger.error("Ошибка соединения с API: %s", e)
            return None

    # -- Keys ------------------------------------------------------------------

    @classmethod
    async def list_keys(cls) -> Optional[dict]:
        return await cls._req("GET", "access-keys")

    @classmethod
    async def get_key(cls, key_id: str) -> Optional[dict]:
        return await cls._req("GET", f"access-keys/{key_id}")

    @classmethod
    async def create_key(
        cls,
        name: Optional[str] = None,
        limit_gb: Optional[float] = None,
    ) -> Optional[dict]:
        body: dict = {}
        if name:
            body["name"] = name
        if limit_gb:
            body["limit"] = {"bytes": int(limit_gb * 1024 ** 3)}
        return await cls._req("POST", "access-keys", json=body or None)

    @classmethod
    async def delete_key(cls, key_id: str) -> bool:
        return await cls._req("DELETE", f"access-keys/{key_id}") is not None

    @classmethod
    async def rename_key(cls, key_id: str, name: str) -> bool:
        return await cls._req(
            "PUT", f"access-keys/{key_id}/name", data={"name": name}
        ) is not None

    @classmethod
    async def set_limit(cls, key_id: str, gb: float) -> bool:
        return await cls._req(
            "PUT",
            f"access-keys/{key_id}/data-limit",
            json={"limit": {"bytes": int(gb * 1024 ** 3)}},
        ) is not None

    @classmethod
    async def remove_limit(cls, key_id: str) -> bool:
        return await cls._req("DELETE", f"access-keys/{key_id}/data-limit") is not None

    # -- Server ----------------------------------------------------------------

    @classmethod
    async def server_info(cls) -> Optional[dict]:
        return await cls._req("GET", "server")

    @classmethod
    async def transfer_stats(cls) -> Optional[dict]:
        return await cls._req("GET", "metrics/transfer")

    @classmethod
    async def set_server_name(cls, name: str) -> bool:
        return await cls._req("PUT", "server/name", json={"name": name}) is not None

    @classmethod
    async def set_server_hostname(cls, hostname: str) -> bool:
        return await cls._req(
            "PUT", "server/hostname-for-access-keys", json={"hostname": hostname}
        ) is not None

    @classmethod
    async def set_server_port(cls, port: int) -> bool:
        return await cls._req(
            "PUT", "server/port-for-new-access-keys", json={"port": port}
        ) is not None

    @classmethod
    async def set_global_limit(cls, gb: float) -> bool:
        return await cls._req(
            "PUT",
            "server/access-key-data-limit",
            json={"limit": {"bytes": int(gb * 1024 ** 3)}},
        ) is not None

    @classmethod
    async def remove_global_limit(cls) -> bool:
        return await cls._req("DELETE", "server/access-key-data-limit") is not None

    @classmethod
    async def set_metrics_enabled(cls, enabled: bool) -> bool:
        return await cls._req(
            "PUT", "metrics/enabled", json={"metricsEnabled": enabled}
        ) is not None


# ── Bot & FSM ─────────────────────────────────────────────────────────────────

bot = Bot(token=Config.BOT_TOKEN)
dp = Dispatcher()


class States(StatesGroup):
    # Ключи
    renaming = State()
    setting_limit = State()
    creating_name = State()
    creating_limit = State()
    # Сервер
    server_name = State()
    server_hostname = State()
    server_port = State()
    server_global_limit = State()
    server_prefix = State()
    prefix_custom = State()


# Пресеты префиксов: имитация TLS-заголовка для обхода DPI
PREFIX_PRESETS: dict[str, tuple[bytes, str]] = {
    "tls10": (bytes([0x16, 0x03, 0x01, 0x00, 0xC2, 0xA8, 0x01, 0x01]), "TLS 1.0"),
    "tls12": (bytes([0x16, 0x03, 0x03, 0x00, 0xC2, 0xA8, 0x01, 0x01]), "TLS 1.2"),
}


def _make_prefixed_url(access_url: str, prefix_bytes: bytes, keep_name: bool = True) -> str:
    """Вставляет параметр prefix= в ss:// ссылку.
    keep_name=True  → сохраняет #name (клиент показывает имя ключа)
    keep_name=False → убирает #name (клиент показывает имя сервера по умолчанию)
    """
    encoded = urllib.parse.quote(prefix_bytes, safe="")
    base, _, fragment = access_url.partition("#")
    sep = "&" if "?" in base else "?"
    result = f"{base}{sep}prefix={encoded}"
    return f"{result}#{fragment}" if (keep_name and fragment) else result


def _next_autoname(keys: list[dict]) -> str:
    """Возвращает следующее свободное имя вида 'PREFIX #N', заполняя пропуски."""
    prefix = Config.KEY_PREFIX
    pattern = re.compile(rf"^{re.escape(prefix)} #(\d+)$")
    used = {int(m.group(1)) for k in keys if (m := pattern.match(k.get("name", "")))}
    n = 1
    while n in used:
        n += 1
    return f"{prefix} #{n}"


# ── Access control ────────────────────────────────────────────────────────────

def admin_only(func):
    @wraps(func)
    async def wrapper(update: types.Message | types.CallbackQuery, *args, **kwargs):
        if update.from_user.id not in Config.ADMINS:
            if isinstance(update, types.Message):
                await update.answer("🔒 Нет доступа")
            else:
                await update.answer("🔒 Нет доступа", show_alert=True)
            return
        return await func(update, *args, **kwargs)
    return wrapper


# ── Keyboards ─────────────────────────────────────────────────────────────────

def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📋 Ключи", callback_data="list_keys"),
            InlineKeyboardButton(text="➕ Создать", callback_data="create_menu"),
        ],
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
            InlineKeyboardButton(text="⚙️ Сервер", callback_data="server_menu"),
        ],
    ])


def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu"),
    ]])


def kb_create() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⚡ Быстро", callback_data="create_quick"),
            InlineKeyboardButton(text="⚙️ С настройками", callback_data="create_advanced"),
        ],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="main_menu")],
    ])


def kb_key(key_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✏️ Переименовать", callback_data=f"rename:{key_id}"),
            InlineKeyboardButton(text="📊 Лимит", callback_data=f"set_limit:{key_id}"),
        ],
        [
            InlineKeyboardButton(text="🔀 Префикс ссылки", callback_data=f"prefix_menu:{key_id}"),
        ],
        [
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"confirm_del:{key_id}"),
            InlineKeyboardButton(text="🔙 Список", callback_data="list_keys"),
        ],
    ])


def kb_prefix_menu(key_id: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=f"🔵 {label}",
            callback_data=f"pfx:{key_id}:{preset}",
        ) for preset, (_, label) in PREFIX_PRESETS.items()],
        [InlineKeyboardButton(text="✏️ Свои байты (hex)", callback_data=f"pfx_custom:{key_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"key:{key_id}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_confirm_del(key_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"delete:{key_id}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"key:{key_id}"),
    ]])


def kb_cancel(back: str = "main_menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data=back),
    ]])


def kb_server(metrics_enabled: bool) -> InlineKeyboardMarkup:
    metrics_label = "📶 Метрики: выключить" if metrics_enabled else "📶 Метрики: включить"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Название сервера", callback_data="srv_rename")],
        [InlineKeyboardButton(text="🌐 Изменить hostname", callback_data="srv_hostname")],
        [InlineKeyboardButton(text="🚪 Порт для новых ключей", callback_data="srv_port")],
        [InlineKeyboardButton(text="🌍 Глобальный лимит", callback_data="srv_global_limit")],
        [InlineKeyboardButton(text=f"🏷 Префикс ключей: {Config.KEY_PREFIX}", callback_data="srv_prefix")],
        [InlineKeyboardButton(text=metrics_label, callback_data="srv_toggle_metrics")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu")],
    ])


def kb_global_limit(has_limit: bool) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="1 GB", callback_data="srv_glimit_set:1"),
            InlineKeyboardButton(text="5 GB", callback_data="srv_glimit_set:5"),
            InlineKeyboardButton(text="10 GB", callback_data="srv_glimit_set:10"),
            InlineKeyboardButton(text="50 GB", callback_data="srv_glimit_set:50"),
        ],
        [InlineKeyboardButton(text="✏️ Своё значение", callback_data="srv_glimit_custom")],
    ]
    if has_limit:
        rows.append([InlineKeyboardButton(text="❌ Снять лимит", callback_data="srv_glimit_remove")])
    rows.append([InlineKeyboardButton(text="🔙 Сервер", callback_data="server_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Formatting ────────────────────────────────────────────────────────────────

def fmt_bytes(b: int | None) -> str:
    if b is None:
        return "♾️ без ограничений"
    value = float(b)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            # Целые числа без дроби, дробные — с одним знаком
            formatted = f"{value:.0f}" if value == int(value) else f"{value:.1f}"
            return f"{formatted} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def fmt_pct(used: int, limit: int | None) -> str:
    if not limit:
        return ""
    return f" ({used / limit * 100:.1f}%)"


def fmt_key(key: dict, used_bytes: int | None = None) -> str:
    limit_bytes = key.get("dataLimit", {}).get("bytes") if "dataLimit" in key else None
    lines = [
        f"🔑 <b>{key.get('name') or 'Без имени'}</b>",
        f"🆔 <code>{key['id']}</code>",
        f"🔒 {key.get('method', 'N/A')}  |  🚪 порт {key.get('port', 'N/A')}",
        f"📊 Лимит: {fmt_bytes(limit_bytes)}",
    ]
    if used_bytes is not None:
        lines.append(
            f"📤 Использовано: {fmt_bytes(used_bytes)}{fmt_pct(used_bytes, limit_bytes)}"
        )
    if url := key.get("accessUrl"):
        lines.append(f"\n🔗 <code>{url}</code>")
    return "\n".join(lines)


# ── Handlers: main menu ───────────────────────────────────────────────────────

@dp.message(CommandStart())
@admin_only
async def cmd_start(msg: Message):
    await msg.answer(
        "🔐 <b>Outline VPN — управление</b>",
        reply_markup=kb_main(),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "main_menu")
@admin_only
async def cb_main_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text(
        "🔐 <b>Outline VPN — управление</b>",
        reply_markup=kb_main(),
        parse_mode="HTML",
    )
    await cb.answer()


# ── Handlers: key list ────────────────────────────────────────────────────────

@dp.callback_query(F.data == "list_keys")
@admin_only
async def cb_list_keys(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    data = await OutlineAPI.list_keys()
    if not data or not data.get("accessKeys"):
        await cb.message.edit_text("❌ Ключей нет", reply_markup=kb_back())
        await cb.answer()
        return

    builder = InlineKeyboardBuilder()
    for key in data["accessKeys"]:
        builder.button(
            text=key.get("name") or f"Ключ {key['id']}",
            callback_data=f"key:{key['id']}",
        )
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="main_menu"))

    await cb.message.edit_text(
        "📋 <b>Список ключей:</b>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML",
    )
    await cb.answer()


@dp.callback_query(F.data.startswith("key:"))
@admin_only
async def cb_key_info(cb: CallbackQuery):
    key_id = cb.data.split(":", 1)[1]
    key, stats = await asyncio.gather(
        OutlineAPI.get_key(key_id),
        OutlineAPI.transfer_stats(),
    )
    if not key:
        await cb.answer("❌ Ключ не найден", show_alert=True)
        return

    used_bytes: int | None = None
    if stats and stats.get("bytesTransferredByUserId"):
        raw = stats["bytesTransferredByUserId"].get(str(key_id))
        if raw is not None:
            used_bytes = int(raw)

    await cb.message.edit_text(
        fmt_key(key, used_bytes),
        reply_markup=kb_key(key_id),
        parse_mode="HTML",
    )
    await cb.answer()


# ── Handlers: create key ──────────────────────────────────────────────────────

@dp.callback_query(F.data == "create_menu")
@admin_only
async def cb_create_menu(cb: CallbackQuery):
    await cb.message.edit_text(
        "➕ <b>Создание ключа:</b>",
        reply_markup=kb_create(),
        parse_mode="HTML",
    )
    await cb.answer()


@dp.callback_query(F.data == "create_quick")
@admin_only
async def cb_create_quick(cb: CallbackQuery):
    existing = await OutlineAPI.list_keys()
    existing_list = existing.get("accessKeys", []) if existing else []
    auto_name = _next_autoname(existing_list)

    key = await OutlineAPI.create_key(name=auto_name)
    if not key or "id" not in key:
        await cb.message.edit_text("❌ Ошибка создания ключа", reply_markup=kb_back())
        await cb.answer()
        return
    await cb.message.edit_text(
        f"✅ <b>Ключ создан!</b>\n\n{fmt_key(key)}",
        reply_markup=kb_key(str(key["id"])),
        parse_mode="HTML",
    )
    await cb.answer()


@dp.callback_query(F.data == "create_advanced")
@admin_only
async def cb_create_advanced(cb: CallbackQuery, state: FSMContext):
    await state.set_state(States.creating_name)
    await cb.message.edit_text(
        "✏️ Введите имя нового ключа:",
        reply_markup=kb_cancel(),
    )
    await cb.answer()


@dp.message(States.creating_name)
@admin_only
async def fsm_creating_name(msg: Message, state: FSMContext):
    name = msg.text.strip()
    if not name:
        await msg.answer("❌ Имя не может быть пустым")
        return
    await state.update_data(name=name)
    await state.set_state(States.creating_limit)
    await msg.answer(
        "📊 Введите лимит в GB (например <code>5.5</code>) или <code>0</code> — без ограничений:",
        parse_mode="HTML",
        reply_markup=kb_cancel(),
    )


@dp.message(States.creating_limit)
@admin_only
async def fsm_creating_limit(msg: Message, state: FSMContext):
    try:
        gb = float(msg.text.strip())
        if gb < 0:
            await msg.answer("❌ Значение не может быть отрицательным")
            return
    except ValueError:
        await msg.answer("❌ Введите число, например <code>5.5</code>", parse_mode="HTML")
        return

    data = await state.get_data()
    await state.clear()

    key = await OutlineAPI.create_key(
        name=data.get("name"),
        limit_gb=gb if gb > 0 else None,
    )
    if not key or "id" not in key:
        await msg.answer("❌ Ошибка создания ключа", reply_markup=kb_main())
        return

    await msg.answer(
        f"✅ <b>Ключ создан!</b>\n\n{fmt_key(key)}",
        reply_markup=kb_key(str(key["id"])),
        parse_mode="HTML",
    )


# ── Handlers: rename key ──────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("rename:"))
@admin_only
async def cb_rename_start(cb: CallbackQuery, state: FSMContext):
    key_id = cb.data.split(":", 1)[1]
    await state.set_state(States.renaming)
    await state.update_data(key_id=key_id)
    await cb.message.edit_text(
        f"✏️ Введите новое имя для ключа <code>{key_id}</code>:",
        parse_mode="HTML",
        reply_markup=kb_cancel(f"key:{key_id}"),
    )
    await cb.answer()


@dp.message(States.renaming)
@admin_only
async def fsm_rename(msg: Message, state: FSMContext):
    name = msg.text.strip()
    if not name:
        await msg.answer("❌ Имя не может быть пустым")
        return
    data = await state.get_data()
    await state.clear()
    ok = await OutlineAPI.rename_key(data["key_id"], name)
    if ok:
        await msg.answer(
            f"✅ Ключ переименован в <b>{name}</b>",
            parse_mode="HTML",
            reply_markup=kb_main(),
        )
    else:
        await msg.answer("❌ Ошибка переименования", reply_markup=kb_main())


# ── Handlers: per-key data limit ─────────────────────────────────────────────

@dp.callback_query(F.data.startswith("set_limit:"))
@admin_only
async def cb_limit_start(cb: CallbackQuery, state: FSMContext):
    key_id = cb.data.split(":", 1)[1]
    await state.set_state(States.setting_limit)
    await state.update_data(key_id=key_id)
    await cb.message.edit_text(
        "📊 Введите лимит в GB (например <code>5.5</code>) или <code>0</code> — снять ограничение:",
        parse_mode="HTML",
        reply_markup=kb_cancel(f"key:{key_id}"),
    )
    await cb.answer()


@dp.message(States.setting_limit)
@admin_only
async def fsm_set_limit(msg: Message, state: FSMContext):
    try:
        gb = float(msg.text.strip())
        if gb < 0:
            await msg.answer("❌ Значение не может быть отрицательным")
            return
    except ValueError:
        await msg.answer("❌ Введите число, например <code>5.5</code>", parse_mode="HTML")
        return

    data = await state.get_data()
    await state.clear()

    if gb == 0:
        ok = await OutlineAPI.remove_limit(data["key_id"])
        text = "✅ Лимит снят" if ok else "❌ Не удалось снять лимит"
    else:
        ok = await OutlineAPI.set_limit(data["key_id"], gb)
        text = f"✅ Лимит установлен: {gb} GB" if ok else "❌ Не удалось установить лимит"

    await msg.answer(text, reply_markup=kb_main())


# ── Handlers: delete key ──────────────────────────────────────────────────────

@dp.callback_query(F.data.startswith("confirm_del:"))
@admin_only
async def cb_confirm_del(cb: CallbackQuery):
    key_id = cb.data.split(":", 1)[1]
    await cb.message.edit_text(
        f"⚠️ <b>Удалить ключ {key_id}?</b>\nДействие необратимо.",
        reply_markup=kb_confirm_del(key_id),
        parse_mode="HTML",
    )
    await cb.answer()


@dp.callback_query(F.data.startswith("delete:"))
@admin_only
async def cb_delete(cb: CallbackQuery):
    key_id = cb.data.split(":", 1)[1]
    ok = await OutlineAPI.delete_key(key_id)
    if ok:
        await cb.message.edit_text(f"✅ Ключ {key_id} удалён", reply_markup=kb_main())
    else:
        await cb.message.edit_text(
            f"❌ Не удалось удалить ключ {key_id}", reply_markup=kb_main()
        )
    await cb.answer()


# ── Handlers: statistics ──────────────────────────────────────────────────────

@dp.callback_query(F.data == "stats")
@admin_only
async def cb_stats(cb: CallbackQuery):
    server, stats, keys_data = await asyncio.gather(
        OutlineAPI.server_info(),
        OutlineAPI.transfer_stats(),
        OutlineAPI.list_keys(),
    )

    key_names: dict[str, str] = {}
    if keys_data and keys_data.get("accessKeys"):
        for k in keys_data["accessKeys"]:
            key_names[str(k["id"])] = k.get("name") or f"Ключ {k['id']}"

    lines = ["<b>📊 Статистика сервера</b>"]

    if server:
        global_limit = server.get("accessKeyDataLimit")
        lines += [
            f"🖥 <b>{server.get('name', 'N/A')}</b>",
            f"🌐 {server.get('hostnameForAccessKeys', server.get('hostname', 'N/A'))}  |  🚪 порт {server.get('portForNewAccessKeys', 'N/A')}",
            f"🔄 Версия: {server.get('version', 'N/A')}",
            f"🌍 Глобальный лимит: {fmt_bytes(global_limit['bytes']) if global_limit else '♾️ нет'}",
            f"📶 Метрики: {'🟢 вкл' if server.get('metricsEnabled') else '🔴 выкл'}",
        ]
        if ts := server.get("createdTimestampMs"):
            lines.append(f"📅 Создан: {datetime.fromtimestamp(ts / 1000):%d.%m.%Y}")
    else:
        lines.append("❌ Не удалось получить информацию о сервере")

    if stats and stats.get("bytesTransferredByUserId"):
        transfer = stats["bytesTransferredByUserId"]
        total = sum(int(b) for b in transfer.values())
        lines.append(f"\n<b>📶 Трафик по ключам:</b>  (всего: {fmt_bytes(total)})")
        for uid, b in sorted(transfer.items(), key=lambda x: int(x[1]), reverse=True):
            name = key_names.get(uid, f"Ключ {uid}")
            lines.append(f"  🔑 {name}: {fmt_bytes(int(b))}")
    else:
        lines.append("\n❌ Статистика трафика недоступна")

    await cb.message.edit_text("\n".join(lines), reply_markup=kb_back(), parse_mode="HTML")
    await cb.answer()


# ── Handlers: server settings ─────────────────────────────────────────────────

@dp.callback_query(F.data == "server_menu")
@admin_only
async def cb_server_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    server = await OutlineAPI.server_info()
    if not server:
        await cb.message.edit_text("❌ Не удалось получить данные сервера", reply_markup=kb_back())
        await cb.answer()
        return

    global_limit = server.get("accessKeyDataLimit")
    metrics_enabled = server.get("metricsEnabled", False)

    lines = [
        "⚙️ <b>Настройки сервера</b>",
        "",
        f"🖥 <b>{server.get('name', 'N/A')}</b>",
        f"🌐 Hostname: <code>{server.get('hostnameForAccessKeys', server.get('hostname', 'N/A'))}</code>",
        f"🚪 Порт новых ключей: <code>{server.get('portForNewAccessKeys', 'N/A')}</code>",
        f"🌍 Глобальный лимит: {fmt_bytes(global_limit['bytes']) if global_limit else '♾️ нет'}",
        f"📶 Метрики: {'🟢 включены' if metrics_enabled else '🔴 выключены'}",
        f"🏷 Префикс ключей: <code>{Config.KEY_PREFIX}</code>",
    ]

    await cb.message.edit_text(
        "\n".join(lines),
        reply_markup=kb_server(metrics_enabled),
        parse_mode="HTML",
    )
    await cb.answer()


# Переименование сервера ───────────────────────────────────────────────────────

@dp.callback_query(F.data == "srv_rename")
@admin_only
async def cb_srv_rename(cb: CallbackQuery, state: FSMContext):
    await state.set_state(States.server_name)
    await cb.message.edit_text(
        "✏️ Введите новое название сервера:",
        reply_markup=kb_cancel("server_menu"),
    )
    await cb.answer()


@dp.message(States.server_name)
@admin_only
async def fsm_server_name(msg: Message, state: FSMContext):
    name = msg.text.strip()
    if not name:
        await msg.answer("❌ Название не может быть пустым")
        return
    await state.clear()
    ok = await OutlineAPI.set_server_name(name)
    text = f"✅ Сервер переименован в <b>{name}</b>" if ok else "❌ Ошибка переименования"
    await msg.answer(text, parse_mode="HTML", reply_markup=kb_main())


# Изменение hostname ───────────────────────────────────────────────────────────

@dp.callback_query(F.data == "srv_hostname")
@admin_only
async def cb_srv_hostname(cb: CallbackQuery, state: FSMContext):
    await state.set_state(States.server_hostname)
    await cb.message.edit_text(
        "🌐 Введите новый hostname (домен или IP):",
        reply_markup=kb_cancel("server_menu"),
    )
    await cb.answer()


@dp.message(States.server_hostname)
@admin_only
async def fsm_server_hostname(msg: Message, state: FSMContext):
    hostname = msg.text.strip()
    if not hostname:
        await msg.answer("❌ Hostname не может быть пустым")
        return
    await state.clear()
    ok = await OutlineAPI.set_server_hostname(hostname)
    text = f"✅ Hostname изменён на <code>{hostname}</code>" if ok else "❌ Ошибка изменения hostname"
    await msg.answer(text, parse_mode="HTML", reply_markup=kb_main())


# Изменение порта для новых ключей ────────────────────────────────────────────

@dp.callback_query(F.data == "srv_port")
@admin_only
async def cb_srv_port(cb: CallbackQuery, state: FSMContext):
    await state.set_state(States.server_port)
    await cb.message.edit_text(
        "🚪 Введите новый порт для новых ключей (1–65535):",
        reply_markup=kb_cancel("server_menu"),
    )
    await cb.answer()


@dp.message(States.server_port)
@admin_only
async def fsm_server_port(msg: Message, state: FSMContext):
    try:
        port = int(msg.text.strip())
        if not 1 <= port <= 65535:
            await msg.answer("❌ Порт должен быть от 1 до 65535")
            return
    except ValueError:
        await msg.answer("❌ Введите целое число, например <code>1496</code>", parse_mode="HTML")
        return
    await state.clear()
    ok = await OutlineAPI.set_server_port(port)
    text = f"✅ Порт для новых ключей изменён на <code>{port}</code>" if ok else "❌ Ошибка изменения порта"
    await msg.answer(text, parse_mode="HTML", reply_markup=kb_main())


# Глобальный лимит ────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "srv_global_limit")
@admin_only
async def cb_srv_global_limit(cb: CallbackQuery):
    server = await OutlineAPI.server_info()
    global_limit = server.get("accessKeyDataLimit") if server else None
    current = fmt_bytes(global_limit["bytes"]) if global_limit else "♾️ нет"

    await cb.message.edit_text(
        f"🌍 <b>Глобальный лимит</b>\n\nТекущий: <b>{current}</b>\n\n"
        "Устанавливается сразу для всех ключей.",
        reply_markup=kb_global_limit(has_limit=bool(global_limit)),
        parse_mode="HTML",
    )
    await cb.answer()


@dp.callback_query(F.data.startswith("srv_glimit_set:"))
@admin_only
async def cb_srv_glimit_set(cb: CallbackQuery):
    gb = float(cb.data.split(":", 1)[1])
    ok = await OutlineAPI.set_global_limit(gb)
    text = f"✅ Глобальный лимит установлен: {gb:.0f} GB" if ok else "❌ Ошибка установки лимита"
    await cb.message.edit_text(text, reply_markup=kb_back())
    await cb.answer()


@dp.callback_query(F.data == "srv_glimit_custom")
@admin_only
async def cb_srv_glimit_custom(cb: CallbackQuery, state: FSMContext):
    await state.set_state(States.server_global_limit)
    await cb.message.edit_text(
        "🌍 Введите глобальный лимит в GB (например <code>10.5</code>):",
        parse_mode="HTML",
        reply_markup=kb_cancel("srv_global_limit"),
    )
    await cb.answer()


@dp.message(States.server_global_limit)
@admin_only
async def fsm_server_global_limit(msg: Message, state: FSMContext):
    try:
        gb = float(msg.text.strip())
        if gb <= 0:
            await msg.answer("❌ Введите положительное число")
            return
    except ValueError:
        await msg.answer("❌ Введите число, например <code>10.5</code>", parse_mode="HTML")
        return
    await state.clear()
    ok = await OutlineAPI.set_global_limit(gb)
    text = f"✅ Глобальный лимит установлен: {gb} GB" if ok else "❌ Ошибка установки лимита"
    await msg.answer(text, reply_markup=kb_main())


@dp.callback_query(F.data == "srv_glimit_remove")
@admin_only
async def cb_srv_glimit_remove(cb: CallbackQuery):
    ok = await OutlineAPI.remove_global_limit()
    text = "✅ Глобальный лимит снят" if ok else "❌ Ошибка снятия лимита"
    await cb.message.edit_text(text, reply_markup=kb_back())
    await cb.answer()


# Метрики ─────────────────────────────────────────────────────────────────────

@dp.callback_query(F.data == "srv_toggle_metrics")
@admin_only
async def cb_toggle_metrics(cb: CallbackQuery):
    server = await OutlineAPI.server_info()
    if not server:
        await cb.answer("❌ Не удалось получить данные сервера", show_alert=True)
        return
    current = server.get("metricsEnabled", False)
    ok = await OutlineAPI.set_metrics_enabled(not current)
    if ok:
        state_str = "включены" if not current else "выключены"
        await cb.answer(f"✅ Метрики {state_str}", show_alert=True)
    else:
        await cb.answer("❌ Ошибка изменения метрик", show_alert=True)
    # Обновляем страницу настроек
    await cb_server_menu(cb, None)


# Префикс ссылки (DPI bypass) ─────────────────────────────────────────────────

def _fmt_prefix_result(access_url: str, prefix_bytes: bytes, label: str) -> str:
    """Формирует сообщение с двумя вариантами ссылки: с именем и без."""
    url_named = _make_prefixed_url(access_url, prefix_bytes, keep_name=True)
    url_clean = _make_prefixed_url(access_url, prefix_bytes, keep_name=False)
    hex_str = prefix_bytes.hex().upper()

    lines = [f"🔀 <b>Ссылка с DPI-префиксом {label}</b>", ""]

    # Показываем ссылку без имени только если оригинал содержал имя
    if url_named != url_clean:
        lines += [
            "👤 <b>Для клиента — без имени ключа</b>",
            f"(клиент покажет имя сервера по умолчанию)",
            f"<code>{url_clean}</code>",
            "",
            "🏷 <b>С именем ключа</b>",
            f"<code>{url_named}</code>",
        ]
    else:
        lines += [
            "🔗 <b>Ссылка:</b>",
            f"<code>{url_clean}</code>",
        ]

    lines += ["", f"🔬 Байты: <code>{hex_str}</code>"]
    return "\n".join(lines)

@dp.callback_query(F.data.startswith("prefix_menu:"))
@admin_only
async def cb_prefix_menu(cb: CallbackQuery):
    key_id = cb.data.split(":", 1)[1]
    await cb.message.edit_text(
        "🔀 <b>Префикс ссылки</b>\n\n"
        "Добавляет байты в начало TCP-соединения, маскируя трафик под TLS.\n"
        "Работает с Outline Client 1.x+\n\n"
        "Выберите пресет или введите байты вручную:",
        reply_markup=kb_prefix_menu(key_id),
        parse_mode="HTML",
    )
    await cb.answer()


@dp.callback_query(F.data.startswith("pfx:"))
@admin_only
async def cb_prefix_apply(cb: CallbackQuery):
    _, key_id, preset = cb.data.split(":", 2)
    if preset not in PREFIX_PRESETS:
        await cb.answer("❌ Неизвестный пресет", show_alert=True)
        return

    prefix_bytes, label = PREFIX_PRESETS[preset]
    key = await OutlineAPI.get_key(key_id)
    if not key or not key.get("accessUrl"):
        await cb.answer("❌ Не удалось получить ключ", show_alert=True)
        return

    await cb.message.edit_text(
        _fmt_prefix_result(key["accessUrl"], prefix_bytes, label),
        reply_markup=kb_prefix_menu(key_id),
        parse_mode="HTML",
    )
    await cb.answer()


@dp.callback_query(F.data.startswith("pfx_custom:"))
@admin_only
async def cb_prefix_custom(cb: CallbackQuery, state: FSMContext):
    key_id = cb.data.split(":", 1)[1]
    await state.set_state(States.prefix_custom)
    await state.update_data(key_id=key_id)
    await cb.message.edit_text(
        "✏️ Введите байты префикса в <b>hex-формате</b>:\n\n"
        "Примеры:\n"
        "• <code>160301</code> — TLS Record header\n"
        "• <code>474554202F20485454502F312E310D0A0D0A</code> — HTTP GET\n\n"
        "Пробелы и двоеточия между байтами допустимы: <code>16 03 01</code>",
        parse_mode="HTML",
        reply_markup=kb_cancel(f"prefix_menu:{key_id}"),
    )
    await cb.answer()


@dp.message(States.prefix_custom)
@admin_only
async def fsm_prefix_custom(msg: Message, state: FSMContext):
    hex_str = msg.text.strip().replace(" ", "").replace(":", "").replace("-", "")
    try:
        prefix_bytes = bytes.fromhex(hex_str)
        if not prefix_bytes:
            raise ValueError
    except ValueError:
        await msg.answer(
            "❌ Неверный hex. Пример: <code>16 03 01 00</code>",
            parse_mode="HTML",
        )
        return

    data = await state.get_data()
    await state.clear()

    key = await OutlineAPI.get_key(data["key_id"])
    if not key or not key.get("accessUrl"):
        await msg.answer("❌ Не удалось получить ключ", reply_markup=kb_main())
        return

    await msg.answer(
        _fmt_prefix_result(key["accessUrl"], prefix_bytes, "custom"),
        parse_mode="HTML",
        reply_markup=kb_key(data["key_id"]),
    )


# Префикс автоимени ───────────────────────────────────────────────────────────

@dp.callback_query(F.data == "srv_prefix")
@admin_only
async def cb_srv_prefix(cb: CallbackQuery, state: FSMContext):
    await state.set_state(States.server_prefix)
    await cb.message.edit_text(
        f"🏷 Текущий префикс: <code>{Config.KEY_PREFIX}</code>\n\n"
        "Введите новый префикс для быстрого создания ключей.\n"
        "Ключи будут называться: <code>ПРЕФИКС #1</code>, <code>ПРЕФИКС #2</code> и т.д.",
        parse_mode="HTML",
        reply_markup=kb_cancel("server_menu"),
    )
    await cb.answer()


@dp.message(States.server_prefix)
@admin_only
async def fsm_server_prefix(msg: Message, state: FSMContext):
    prefix = msg.text.strip()
    if not prefix:
        await msg.answer("❌ Префикс не может быть пустым")
        return
    if len(prefix) > 32:
        await msg.answer("❌ Префикс не может быть длиннее 32 символов")
        return
    await state.clear()
    Config.KEY_PREFIX = prefix
    await msg.answer(
        f"✅ Префикс изменён на <code>{prefix}</code>\n"
        f"Следующий быстрый ключ получит имя: <code>{prefix} #1</code>",
        parse_mode="HTML",
        reply_markup=kb_main(),
    )


# ── Global error handler ──────────────────────────────────────────────────────

@dp.errors()
async def on_error(event: types.ErrorEvent):
    logger.exception(
        "Необработанная ошибка: %s | update=%s", event.exception, event.update
    )


# ── Entrypoint ────────────────────────────────────────────────────────────────

async def main():
    logger.info("Запуск бота (admins: %s)", Config.ADMINS)
    await OutlineAPI.start()
    try:
        await dp.start_polling(bot)
    finally:
        await OutlineAPI.stop()
        await bot.session.close()
        logger.info("Бот остановлен")


if __name__ == "__main__":
    asyncio.run(main())
