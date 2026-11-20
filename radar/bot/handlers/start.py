"""/start, onboarding (3 steps), main menu, radar switch, help, stats."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from radar.bot import views
from radar.bot.callbacks import Menu, Ob
from radar.bot.states import Onboarding
from radar.bot.ui import USER_TEXT, show
from radar.db import User
from radar.matching import effective_filters, parse_phrases
from radar.services import Services

PREVIEW = 3


async def menu_screen(svc: Services, user: User) -> views.Screen:
    access = await svc.billing.access(user.id)
    remaining = await svc.usage.remaining(user.id)
    return views.main_menu(user, access, remaining, effective_filters(user, access.plan))


async def home_or_onboarding(target, svc: Services, user: User, state: FSMContext) -> None:
    await state.set_state(None)
    user = await svc.db.get_user(user.id)
    if not user.onboarded:
        plan = await svc.billing.plan(user.id)
        return await show(target, views.categories_screen("ob", user.cats, plan))
    await show(target, await menu_screen(svc, user))


async def cmd_start(message: Message, svc: Services, user: User, state: FSMContext) -> None:
    await svc.account.event(user.id, "start")
    await home_or_onboarding(message, svc, user, state)


async def cb_home(cb: CallbackQuery, svc: Services, user: User, state: FSMContext) -> None:
    await home_or_onboarding(cb, svc, user, state)


async def cb_radar(cb: CallbackQuery, svc: Services, user: User, state: FSMContext) -> None:
    user = await svc.db.get_user(user.id)
    if not user.onboarded:
        return await home_or_onboarding(cb, svc, user, state)
    on = 0 if user.radar_on else 1
    await svc.db.update_user(user.id, radar_on=on)
    await cb.answer("📡 Радар включён — новые заказы придут сюда" if on else "📡 Радар выключен")
    await show(cb, await menu_screen(svc, await svc.db.get_user(user.id)))


async def cmd_radar(message: Message, svc: Services, user: User) -> None:
    await show(message, await menu_screen(svc, user))


# --- onboarding --------------------------------------------------------------------------------------

async def cb_onboarding(cb: CallbackQuery, callback_data: Ob, svc: Services, user: User, state: FSMContext) -> None:
    user = await svc.db.get_user(user.id)
    plan = await svc.billing.plan(user.id)
    act = callback_data.act
    if act == "cats":
        await state.set_state(None)
        return await show(cb, views.categories_screen("ob", user.cats, plan))
    if act == "kw":
        if not user.cats:
            return await cb.answer("Выбери хотя бы одну категорию 👆", show_alert=True)
        await state.set_state(Onboarding.keywords)
        return await show(cb, views.onboarding_keywords(user.kws, plan))
    if act == "skip":
        await state.set_state(None)
        return await show(cb, views.budget_screen("ob", user, plan))


async def msg_onboarding_keywords(message: Message, svc: Services, user: User, state: FSMContext) -> None:
    plan = await svc.billing.plan(user.id)
    words = parse_phrases(message.text or "")
    if not words:
        return await message.answer("Не вижу слов 🤔 Напиши через запятую или нажми «Пропустить».")
    note = ""
    if plan.max_keywords is not None and len(words) > plan.max_keywords:
        words = words[: plan.max_keywords]
        note = f"На FREE — до {plan.max_keywords} слов, сохранил первые: {', '.join(words)}. В 💎 PRO — без лимита.\n\n"
    await svc.db.update_user(user.id, keywords=words)
    await state.set_state(None)
    text, markup = views.budget_screen("ob", await svc.db.get_user(user.id), plan)
    await show(message, (views.h(note) + text, markup))


async def finish_onboarding(cb: CallbackQuery, svc: Services, user_id: int) -> None:
    await svc.db.update_user(user_id, onboarded=1, radar_on=1)
    await svc.account.event(user_id, "filters")
    user = await svc.db.get_user(user_id)
    plan = await svc.billing.plan(user_id)
    found = await svc.orders.matching(effective_filters(user, plan))
    await show(cb, views.onboarding_done(found[:PREVIEW], len(found)))


# --- help, stats, legal ----------------------------------------------------------------------------------

async def cb_help(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(None)
    await show(cb, views.help_screen())


async def cmd_help(message: Message) -> None:
    await show(message, views.help_screen())


async def cb_terms(cb: CallbackQuery, svc: Services) -> None:
    await show(cb, views.terms_screen(svc.settings.support_contact))


async def cmd_terms(message: Message, svc: Services) -> None:
    await show(message, views.terms_screen(svc.settings.support_contact))


async def cb_privacy(cb: CallbackQuery) -> None:
    await show(cb, views.privacy_screen())


async def cmd_privacy(message: Message) -> None:
    await show(message, views.privacy_screen())


async def _stats(target, svc: Services, user: User) -> None:
    user = await svc.db.get_user(user.id)
    plan = await svc.billing.plan(user.id)
    await show(target, views.stats_screen(await svc.account.stats(user.id, effective_filters(user, plan), svc.orders)))


async def cb_stats(cb: CallbackQuery, svc: Services, user: User) -> None:
    await _stats(cb, svc, user)


async def cmd_stats(message: Message, svc: Services, user: User) -> None:
    await _stats(message, svc, user)


async def cmd_cancel(message: Message, svc: Services, user: User, state: FSMContext) -> None:
    await home_or_onboarding(message, svc, user, state)


def create_router() -> Router:
    r = Router(name="start")
    r.message.register(cmd_start, CommandStart())
    r.message.register(cmd_cancel, Command("menu", "cancel"))
    r.message.register(cmd_radar, Command("radar"))
    r.message.register(cmd_help, Command("help"))
    r.message.register(cmd_terms, Command("terms"))
    r.message.register(cmd_privacy, Command("privacy"))
    r.message.register(cmd_stats, Command("stats"))
    r.callback_query.register(cb_home, Menu.filter(F.to.in_({"home", "cancel"})))
    r.callback_query.register(cb_radar, Menu.filter(F.to == "radar"))
    r.callback_query.register(cb_help, Menu.filter(F.to == "help"))
    r.callback_query.register(cb_terms, Menu.filter(F.to == "terms"))
    r.callback_query.register(cb_privacy, Menu.filter(F.to == "privacy"))
    r.callback_query.register(cb_stats, Menu.filter(F.to == "stats"))
    r.callback_query.register(cb_onboarding, Ob.filter())
    r.message.register(msg_onboarding_keywords, Onboarding.keywords, USER_TEXT)
    return r
