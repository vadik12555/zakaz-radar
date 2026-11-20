"""Filters (categories, keywords, exclusions, budget, quiet hours) and the profile with the response template."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from radar.bot import views
from radar.bot.callbacks import Bud, Cat, Flt, Menu, Prof
from radar.bot.handlers.start import finish_onboarding
from radar.bot.states import FilterInput, ProfileInput
from radar.bot.ui import USER_TEXT, show
from radar.db import User
from radar.fl import CATEGORIES
from radar.matching import parse_phrases
from radar.services import Services
from radar.templates import default_template_text

BUDGET_LOCKED = "🔒 Фильтр по бюджету — в 💎 PRO. На FREE приходят заказы с любым бюджетом."


async def _filters(target, svc: Services, user_id: int, state: FSMContext | None = None) -> None:
    if state:
        await state.set_state(None)
    user = await svc.db.get_user(user_id)
    await show(target, views.filters_screen(user, await svc.billing.access(user_id)))


async def cb_filters(cb: CallbackQuery, svc: Services, user: User, state: FSMContext) -> None:
    await _filters(cb, svc, user.id, state)


async def cmd_filters(message: Message, svc: Services, user: User, state: FSMContext) -> None:
    await _filters(message, svc, user.id, state)


async def cb_flt(cb: CallbackQuery, callback_data: Flt, svc: Services, user: User, state: FSMContext) -> None:
    user = await svc.db.get_user(user.id)
    plan = await svc.billing.plan(user.id)
    act = callback_data.act
    if act == "cats":
        await state.set_state(None)
        return await show(cb, views.categories_screen("f", user.cats, plan))
    if act in ("kw", "ex"):
        await state.set_state(FilterInput.keywords if act == "kw" else FilterInput.excludes)
        return await show(cb, views.words_screen(act, user.kws if act == "kw" else user.excl, plan))
    if act in ("kw_clear", "ex_clear"):
        kind = act[:2]
        await svc.db.update_user(user.id, **{"keywords" if kind == "kw" else "excludes": []})
        await cb.answer("🧹 Очищено")
        return await show(cb, views.words_screen(kind, [], plan))
    if act == "budget":
        await state.set_state(None)
        return await show(cb, views.budget_screen("f", user, plan))
    if act == "nobud":
        if not plan.budget_filter:
            return await cb.answer(BUDGET_LOCKED, show_alert=True)
        await svc.db.update_user(user.id, show_no_budget=0 if user.show_no_budget else 1)
        await cb.answer("Заказы без бюджета: " + ("скрываю" if user.show_no_budget else "показываю"))
        return await _filters(cb, svc, user.id)
    if act == "quiet":
        await svc.db.update_user(user.id, quiet=0 if user.quiet else 1)
        await cb.answer("🌙 Ночью (23:00–08:00 МСК) уведомлений не будет" if not user.quiet
                        else "🌙 Тихие часы выключены")
        return await _filters(cb, svc, user.id)


async def msg_words(message: Message, svc: Services, user: User, state: FSMContext) -> None:
    kind = "kw" if await state.get_state() == FilterInput.keywords.state else "ex"
    plan = await svc.billing.plan(user.id)
    words = parse_phrases(message.text or "")
    if not words:
        return await message.answer("Не вижу слов 🤔 Напиши через запятую, например: <code>telegram бот, парсер</code>")
    note = ""
    if kind == "kw" and plan.max_keywords is not None and len(words) > plan.max_keywords:
        words = words[: plan.max_keywords]
        note = f"На FREE — до {plan.max_keywords} слов, сохранил первые. В 💎 PRO — без лимита.\n"
    await svc.db.update_user(user.id, **{"keywords" if kind == "kw" else "excludes": words})
    await state.set_state(None)
    await message.answer(note + "✅ Сохранено.")
    await _filters(message, svc, user.id)


async def cb_cat(cb: CallbackQuery, callback_data: Cat, svc: Services, user: User) -> None:
    user = await svc.db.get_user(user.id)
    plan = await svc.billing.plan(user.id)
    cats, cid = list(user.cats), callback_data.id
    if cid == 0:
        if plan.max_categories is not None:
            return await cb.answer("Все категории сразу — в 💎 PRO", show_alert=True)
        cats = list(CATEGORIES)
    elif cid == -1:
        cats = []
    elif cid not in CATEGORIES:
        return await cb.answer("Такой категории нет", show_alert=True)
    elif cid in cats:
        cats.remove(cid)
    elif plan.max_categories is not None and len(cats) >= plan.max_categories:
        cats = [cid]
        await cb.answer(f"На FREE — {plan.max_categories} категория: заменил выбор. В 💎 PRO — все сразу.")
    else:
        cats.append(cid)
    await svc.db.update_user(user.id, categories=cats)
    await show(cb, views.categories_screen(callback_data.ctx, cats, plan))


async def cb_budget(cb: CallbackQuery, callback_data: Bud, svc: Services, user: User) -> None:
    plan = await svc.billing.plan(user.id)
    val = callback_data.val
    if val != 0 and not plan.budget_filter:
        return await cb.answer(BUDGET_LOCKED, show_alert=True)
    if val == -1:
        await svc.db.update_user(user.id, min_budget=0, show_no_budget=0)
    elif val == 0:
        await svc.db.update_user(user.id, min_budget=0, show_no_budget=1)
    else:
        await svc.db.update_user(user.id, min_budget=val)
    if callback_data.ctx == "ob":
        return await finish_onboarding(cb, svc, user.id)
    await cb.answer("💰 Бюджет сохранён")
    await _filters(cb, svc, user.id)


# --- profile & template ----------------------------------------------------------------------------------

async def _profile(target, svc: Services, user_id: int, state: FSMContext | None = None) -> None:
    if state:
        await state.set_state(None)
    await show(target, views.profile_screen(await svc.db.get_user(user_id), await svc.billing.access(user_id)))


async def cb_profile(cb: CallbackQuery, svc: Services, user: User, state: FSMContext) -> None:
    await _profile(cb, svc, user.id, state)


async def cmd_profile(message: Message, svc: Services, user: User, state: FSMContext) -> None:
    await _profile(message, svc, user.id, state)


async def _template(target, svc: Services, user_id: int) -> None:
    user = await svc.db.get_user(user_id)
    await show(target, views.template_screen(user.template or default_template_text(), bool(user.template)))


async def cb_prof(cb: CallbackQuery, callback_data: Prof, svc: Services, user: User, state: FSMContext) -> None:
    user = await svc.db.get_user(user.id)
    act, field = callback_data.act, callback_data.field
    if act in ("edit", "clear"):
        if field not in views.PROFILE_FIELDS:
            return await cb.answer("Такого поля нет", show_alert=True)
        if act == "clear":
            await svc.db.update_user(user.id, **{field: None})
            await cb.answer("🧹 Очищено")
        await state.set_state(ProfileInput.field)
        await state.update_data(field=field)
        return await show(cb, views.profile_field_screen(field, None if act == "clear" else getattr(user, field)))
    plan = await svc.billing.plan(user.id)
    if not plan.custom_templates:
        await state.set_state(None)
        return await show(cb, views.free_template_locked())
    if act == "tpl":
        await state.set_state(None)
        return await _template(cb, svc, user.id)
    if act == "tpl_edit":
        await state.set_state(ProfileInput.template)
        return await show(cb, views.ask_template())
    if act == "tpl_reset":
        await svc.db.update_user(user.id, template=None)
        await cb.answer("↩️ Стандартный шаблон")
        return await _template(cb, svc, user.id)


async def msg_profile_field(message: Message, svc: Services, user: User, state: FSMContext) -> None:
    field = (await state.get_data()).get("field")
    if field not in views.PROFILE_FIELDS:
        return await _profile(message, svc, user.id, state)
    label, limit = views.PROFILE_FIELDS[field]
    value = " ".join((message.text or "").split())
    if not value or len(value) > limit:
        return await message.answer(f"Нужно от 1 до {limit} символов. Попробуй короче.")
    await svc.db.update_user(user.id, **{field: value})
    await message.answer(f"✅ {label} сохранено.")
    await _profile(message, svc, user.id, state)


async def msg_template(message: Message, svc: Services, user: User, state: FSMContext) -> None:
    if not (await svc.billing.plan(user.id)).custom_templates:
        await state.set_state(None)
        return await show(message, views.free_template_locked())
    text = (message.text or "").strip()
    if not text or len(text) > 500:
        return await message.answer("Нужно от 1 до 500 символов.")
    await svc.db.update_user(user.id, template=text)
    await state.set_state(None)
    await message.answer("✅ Шаблон сохранён.")
    await _template(message, svc, user.id)


def create_router() -> Router:
    r = Router(name="settings")
    r.message.register(cmd_filters, Command("filters"))
    r.message.register(cmd_profile, Command("profile"))
    r.callback_query.register(cb_filters, Menu.filter(F.to == "filters"))
    r.callback_query.register(cb_flt, Flt.filter())
    r.callback_query.register(cb_cat, Cat.filter())
    r.callback_query.register(cb_budget, Bud.filter())
    r.message.register(msg_words, FilterInput.keywords, USER_TEXT)
    r.message.register(msg_words, FilterInput.excludes, USER_TEXT)
    r.callback_query.register(cb_profile, Menu.filter(F.to == "profile"))
    r.callback_query.register(cb_prof, Prof.filter())
    r.message.register(msg_profile_field, ProfileInput.field, USER_TEXT)
    r.message.register(msg_template, ProfileInput.template, USER_TEXT)
    return r
