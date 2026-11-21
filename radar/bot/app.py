"""Bot assembly, logging and the long-polling entry point (structure from Leadly)."""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import re
from html import escape

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.types import BotCommand
from aiogram.utils.backoff import BackoffConfig

from radar.bot.handlers import admin, billing, fallback, orders, settings as settings_handlers, start, support
from radar.bot.jobs import delivery_loop, poll_loop
from radar.bot.storage import SQLiteStorage
from radar.bot.ui import CallbackAnswerMiddleware, ThrottleMiddleware, UserMiddleware
from radar.config import Settings
from radar.db import Database
from radar.services import Services, build_services

log = logging.getLogger("radar")

COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="search", description="🔎 Поиск заказов"),
    BotCommand(command="filters", description="⚙️ Фильтры"),
    BotCommand(command="radar", description="📡 Радар"),
    BotCommand(command="favorites", description="⭐ Избранное"),
    BotCommand(command="profile", description="👤 Профиль"),
    BotCommand(command="stats", description="📊 Статистика"),
    BotCommand(command="pro", description="💎 PRO"),
    BotCommand(command="help", description="❓ Помощь"),
    BotCommand(command="support", description="💬 Поддержка"),
    BotCommand(command="paysupport", description="Вопросы по оплате"),
    BotCommand(command="terms", description="Условия"),
    BotCommand(command="privacy", description="Конфиденциальность"),
    BotCommand(command="cancel", description="Отменить действие"),
]


class RedactSecrets(logging.Filter):
    """Masks the bot token if it ever ends up in a log line."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        self.patterns = [re.escape(s) for s in secrets if s and len(s) > 8]
        self.patterns.append(r"\d{6,}:[A-Za-z0-9_-]{30,}")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for p in self.patterns:
            msg = re.sub(p, "***", msg)
        record.msg, record.args = msg, None
        return True


class AdminAlertHandler(logging.Handler):
    """Forwards ERROR log records to admins in Telegram (batched, at most once per `interval` seconds)."""

    def __init__(self, interval: float = 120.0):
        super().__init__(level=logging.ERROR)
        self.interval = interval
        self.queue: asyncio.Queue | None = None
        self.loop: asyncio.AbstractEventLoop | None = None

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop, self.queue = loop, asyncio.Queue(maxsize=200)

    def emit(self, record: logging.LogRecord) -> None:
        if not self.loop or record.name.startswith("radar.alerts"):
            return
        line = f"{record.name}: {record.getMessage()}"[:400]
        try:
            self.loop.call_soon_threadsafe(self._put, line)
        except RuntimeError:
            pass

    def _put(self, line: str) -> None:
        if self.queue and not self.queue.full():
            self.queue.put_nowait(line)

    async def run(self, bot: Bot, admin_ids: frozenset[int]) -> None:
        alert_log = logging.getLogger("radar.alerts")
        while True:
            first = await self.queue.get()
            await asyncio.sleep(5)
            lines = [first]
            while not self.queue.empty():
                lines.append(self.queue.get_nowait())
            text = (f"🧯 <b>ZakazHunt: ошибки ({len(lines)})</b>\n\n"
                    + "\n".join(f"<code>{escape(x)}</code>" for x in lines[-5:]))
            for admin_id in admin_ids:
                try:
                    await bot.send_message(admin_id, text)
                except Exception as e:
                    alert_log.warning("could not alert admin %s: %s", admin_id, type(e).__name__)
            await asyncio.sleep(self.interval)


ALERTS = AdminAlertHandler()


def setup_logging(settings: Settings) -> None:
    root = logging.getLogger()
    root.setLevel(settings.log_level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    redact = RedactSecrets([settings.bot_token])
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if settings.log_file:
        settings.log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(settings.log_file, maxBytes=5_000_000, backupCount=3,
                                                             encoding="utf-8"))
    handlers.append(ALERTS)
    for hnd in handlers:
        hnd.setFormatter(fmt)
        hnd.addFilter(redact)
        root.addHandler(hnd)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


def bot_defaults() -> DefaultBotProperties:
    return DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True)


def create_dispatcher(svc: Services, throttle: bool = True) -> Dispatcher:
    dp = Dispatcher(storage=SQLiteStorage(svc.db), svc=svc)
    if throttle:
        dp.update.outer_middleware(ThrottleMiddleware(exempt=svc.settings.admin_ids))
    dp.update.outer_middleware(UserMiddleware(svc))
    dp.callback_query.middleware(CallbackAnswerMiddleware())
    dp.include_routers(
        start.create_router(),
        admin.create_router(),
        billing.create_router(),
        support.create_router(),
        settings_handlers.create_router(),
        orders.create_router(),
        fallback.create_router(),  # must stay last
    )
    dp.errors.register(fallback.on_error)
    return dp


def system_proxy() -> str | None:
    """Proxy from HTTPS_PROXY/ALL_PROXY or Windows system settings (where VPN apps put it)."""
    import urllib.request
    proxies = urllib.request.getproxies()
    proxy = proxies.get("https") or proxies.get("all") or proxies.get("http")
    if not proxy:
        return None
    if "://" not in proxy:
        proxy = "http://" + proxy
    return proxy.replace("socks://", "socks5://", 1)


async def run_polling(settings: Settings) -> int:
    if not settings.bot_token:
        log.error("BOT_TOKEN не задан. Скопируй .env.example в .env и впиши токен от @BotFather.")
        return 2
    db = Database(settings.db_path)
    await db.connect()
    svc = build_services(settings, db)
    proxy = settings.telegram_proxy or system_proxy()
    if proxy:
        log.info("подключение к Telegram через прокси %s", proxy.split("@")[-1])
    bot = Bot(settings.bot_token, session=AiohttpSession(proxy=proxy, timeout=60), default=bot_defaults())
    tasks: list[asyncio.Task] = []
    try:
        try:
            me = await bot.get_me()
        except TelegramUnauthorizedError:
            log.error("Telegram отклонил BOT_TOKEN. Проверь токен в @BotFather (/mybots → API Token).")
            return 2
        except TelegramNetworkError as e:
            log.error("Нет связи с api.telegram.org (%s): %s", type(e).__name__, e.message[:300])
            return 3
        await bot.set_my_commands(COMMANDS)
        dp = create_dispatcher(svc)
        tasks.append(asyncio.create_task(poll_loop(bot, svc)))
        tasks.append(asyncio.create_task(delivery_loop(bot, svc)))
        if settings.admin_ids:
            ALERTS.attach(asyncio.get_running_loop())
            tasks.append(asyncio.create_task(ALERTS.run(bot, settings.admin_ids)))
        log.info("ZakazHunt запущен как @%s · оплата Stars: %s · админов: %s", me.username,
                 "вкл" if settings.payments_enabled else "выкл", len(settings.admin_ids) or "не заданы")
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types(),
                               backoff_config=BackoffConfig(min_delay=1, max_delay=60, factor=1.6, jitter=0.1),
                               handle_signals=True)
        return 0
    finally:
        for t in tasks:
            t.cancel()
        await bot.session.close()
        await db.close()
        log.info("ZakazHunt остановлен")
