"""Catch-alls: stray text, stale buttons, unhandled errors."""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import CallbackQuery, ErrorEvent, Message

from radar.bot import views
from radar.bot.handlers.start import menu_screen
from radar.bot.ui import show
from radar.db import User
from radar.services import Services

log = logging.getLogger(__name__)


async def any_message(message: Message, svc: Services, user: User) -> None:
    if not user.onboarded:
        return await message.answer("Нажми /start, чтобы настроить фильтры 🙂")
    menu = (await menu_screen(svc, user))[1]
    await show(message, ("Я работаю через кнопки 👇 Если что-то пошло не так — /cancel.", menu))


async def stale_button(cb: CallbackQuery, svc: Services, user: User) -> None:
    await cb.answer("Эта кнопка устарела — открыл главное меню")
    await show(cb, await menu_screen(svc, user))


async def on_error(event: ErrorEvent) -> bool:
    log.error("Unhandled error in update %s: %s", event.update.update_id, event.exception,
              exc_info=event.exception)
    upd = event.update
    try:
        if upd.callback_query:
            await upd.callback_query.answer("⚠️ Что-то пошло не так. Попробуй ещё раз.", show_alert=True)
        elif upd.message:
            await upd.message.answer("⚠️ Что-то пошло не так. Попробуй ещё раз или /menu.",
                                     reply_markup=views.kb([views.home_btn()]))
    except Exception:
        log.debug("could not report error to user", exc_info=True)
    return True


def create_router() -> Router:
    r = Router(name="fallback")
    r.message.register(any_message)
    r.callback_query.register(stale_button)
    return r
