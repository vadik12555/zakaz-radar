"""Background jobs: feed polling, Radar deliveries, "no data" alert to admins, cleanup."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter

from radar.bot import views
from radar.bot.handlers.orders import card_screen
from radar.services import Services
from radar.utils import in_quiet_hours, now_iso, now_utc, parse_iso, today_msk

log = logging.getLogger(__name__)

DELIVERY_SECONDS = 30
CLEANUP_EVERY = 3600


async def deliver_due(bot: Bot, svc: Services, pause: float = 0.05) -> int:
    """Send Radar cards whose time has come. FREE: after the delay, at most N a day; quiet hours respected."""
    sent = 0
    now = now_iso()
    quiet_now = in_quiet_hours()
    cache: dict[int, tuple] = {}
    for d in await svc.orders.pending_deliveries():
        uid = d["user_id"]
        if uid not in cache:
            cache[uid] = (await svc.db.get_user(uid), await svc.billing.access(uid))
        user, access = cache[uid]
        if not user or not user.radar_on or user.blocked:
            await svc.orders.mark_delivery(uid, d["order_id"], "skipped")
            continue
        if not access.is_pro and d["due_at"] > now:
            continue  # FREE: PRO users get it first
        if user.quiet and quiet_now:
            await svc.orders.mark_delivery(uid, d["order_id"], "quiet")
            continue
        limit = access.plan.notifications_per_day
        if limit is not None and await svc.orders.sent_today(uid) >= limit:
            await svc.orders.mark_delivery(uid, d["order_id"], "limited")
            if user.limit_note_day != today_msk():
                await svc.db.update_user(uid, limit_note_day=today_msk())
                user.limit_note_day = today_msk()
                text, markup = views.daily_limit_note(limit)
                try:
                    await bot.send_message(uid, text, reply_markup=markup)
                except Exception as e:
                    log.info("limit note to %s failed: %s", uid, type(e).__name__)
            continue
        order = await svc.orders.get(d["order_id"])
        if not order:
            await svc.orders.mark_delivery(uid, d["order_id"], "skipped")
            continue
        await svc.orders.mark_delivery(uid, d["order_id"], "sent")  # before sending: never twice
        text, markup = await card_screen(svc, user, order, "r", 0, 0)
        try:
            await bot.send_message(uid, "📡 <b>Новый заказ</b>\n\n" + text, reply_markup=markup)
            sent += 1
        except TelegramForbiddenError:
            await svc.db.update_user(uid, blocked=1)
            await svc.orders.mark_delivery(uid, d["order_id"], "failed")
            user.blocked = 1
        except TelegramRetryAfter as e:
            await svc.db.execute("UPDATE deliveries SET status = 'pending' WHERE user_id = ? AND order_id = ?",
                                 uid, d["order_id"])
            await asyncio.sleep(e.retry_after + 1)
            return sent
        except TelegramNetworkError:
            await svc.db.execute("UPDATE deliveries SET status = 'pending' WHERE user_id = ? AND order_id = ?",
                                 uid, d["order_id"])
            return sent
        except Exception as e:
            log.warning("radar card to %s failed: %s", uid, e)
            await svc.orders.mark_delivery(uid, d["order_id"], "failed")
        await asyncio.sleep(pause)
    return sent


async def check_stale(bot: Bot, svc: Services, started_at: str) -> str | None:
    """Alert admins once when there was no successful poll for STALE_MINUTES; tell them when it's back."""
    last_ok = await svc.db.kv_get("last_ok_at") or started_at
    minutes = int((now_utc() - parse_iso(last_ok)).total_seconds() // 60)
    alerted = await svc.db.kv_get("stale_alerted")
    text = None
    if minutes >= svc.settings.stale_minutes and alerted != last_ok:
        await svc.db.kv_set("stale_alerted", last_ok)
        err = await svc.db.kv_get("last_error") or "нет данных"
        text = (f"🛰 <b>Лента FL.ru: нет данных {minutes} мин</b>\n\nПоследняя ошибка: <code>{views.h(err)}</code>\n"
                "Бот продолжает пробовать с увеличивающейся паузой. Статус: /admin")
    elif minutes < svc.settings.stale_minutes and alerted and alerted != "ok":
        await svc.db.kv_set("stale_alerted", "ok")
        text = "✅ Лента FL.ru снова отвечает, заказы идут."
    if text:
        for admin in svc.settings.admin_ids:
            try:
                await bot.send_message(admin, text)
            except Exception as e:
                log.warning("stale alert to %s failed: %s", admin, type(e).__name__)
    return text


async def poll_loop(bot: Bot, svc: Services) -> None:
    started = now_iso()
    try:
        await svc.orders.bootstrap()
    except Exception:
        log.exception("bootstrap failed")
    last_cleanup = 0.0
    loop = asyncio.get_running_loop()
    while True:
        try:
            result = await svc.orders.poll_once()
            if result.ok:
                log.info("feed: %s items, %s new, %s queued for Radar", result.fetched, result.new, result.queued)
            await check_stale(bot, svc, started)
            if loop.time() - last_cleanup > CLEANUP_EVERY:
                removed = await svc.orders.cleanup()
                last_cleanup = loop.time()
                if removed:
                    log.info("cleanup: %s old orders removed", removed)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("poll loop iteration failed")
        await asyncio.sleep(await svc.orders.next_delay())


async def delivery_loop(bot: Bot, svc: Services) -> None:
    while True:
        try:
            await deliver_due(bot, svc)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("radar delivery failed")
        await asyncio.sleep(DELIVERY_SECONDS)
