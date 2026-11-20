"""Search (3 free searches on FREE), results with paging, order card and its actions, favorites."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from radar.bot import views
from radar.bot.callbacks import FavL, Menu, Ord, PageInfo, Res, Srch
from radar.bot.states import SearchInput
from radar.bot.ui import USER_TEXT, show
from radar.db import User
from radar.matching import effective_filters, exclude_candidates
from radar.services import Services
from radar.services.orders import OrderService
from radar.templates import profile_empty, render_response
from radar.utils import minutes_between, parse_iso

PAGE = views.PAGE_SIZE


async def _paywall(target, svc: Services, user: User) -> None:
    plan = await svc.billing.plan(user.id)
    week = await svc.orders.matching(effective_filters(user, plan), days=7)
    await show(target, views.paywall(len(week), svc.settings.product.for_sale, svc.settings.payments_enabled))


async def _search_home(target, svc: Services, user: User, state: FSMContext) -> None:
    await state.set_state(None)
    user = await svc.db.get_user(user.id)
    access = await svc.billing.access(user.id)
    remaining = await svc.usage.remaining(user.id)
    if remaining == 0:
        return await _paywall(target, svc, user)
    await show(target, views.search_home(access, remaining, await svc.orders.count(),
                                         effective_filters(user, access.plan)))


async def cb_search_home(cb: CallbackQuery, svc: Services, user: User, state: FSMContext) -> None:
    await _search_home(cb, svc, user, state)


async def cmd_search(message: Message, svc: Services, user: User, state: FSMContext) -> None:
    await _search_home(message, svc, user, state)


async def run_search(target, svc: Services, user: User, query: str | None) -> None:
    user = await svc.db.get_user(user.id)
    access = await svc.billing.access(user.id)
    f = OrderService.query_filters(query) if query else effective_filters(user, access.plan)
    if query and not f.keywords:
        return await show(target, views.ask_query())
    ref = await svc.orders.search_ref(user.id, f, query)
    if not (await svc.usage.peek(user.id, ref)).ok:
        return await _paywall(target, svc, user)
    found = await svc.orders.matching(f)
    if not found:  # an empty search is not charged
        return await show(target, views.nothing_found(query))
    quota = await svc.usage.charge(user.id, ref)
    if not quota.ok:
        return await _paywall(target, svc, user)
    await svc.account.event(user.id, "search")
    sid = await svc.orders.save_search(user.id, query, [o["id"] for o in found])
    search = await svc.orders.get_search(user.id, sid)
    await show(target, views.results_screen(search, found[:PAGE], 0, len(found),
                                            None if access.is_pro else quota.remaining))


async def cb_search(cb: CallbackQuery, callback_data: Srch, svc: Services, user: User, state: FSMContext) -> None:
    if callback_data.act == "q":
        if await svc.usage.remaining(user.id) == 0:
            return await _paywall(cb, svc, user)
        await state.set_state(SearchInput.query)
        return await show(cb, views.ask_query())
    await state.set_state(None)
    await run_search(cb, svc, user, None)


async def msg_query(message: Message, svc: Services, user: User, state: FSMContext) -> None:
    await state.set_state(None)
    await run_search(message, svc, user, " ".join((message.text or "").split())[:100])


async def cb_results(cb: CallbackQuery, callback_data: Res, svc: Services, user: User) -> None:
    search = await svc.orders.get_search(user.id, callback_data.sid)
    if not search:
        return await show(cb, views.search_expired())
    ids = search["order_ids"]
    pages = views.pages_of(len(ids))
    page = min(max(0, callback_data.page), pages - 1)
    orders = await svc.orders.many(ids[page * PAGE:(page + 1) * PAGE])
    access = await svc.billing.access(user.id)
    remaining = None if access.is_pro else await svc.usage.remaining(user.id)
    await show(cb, views.results_screen(search, orders, page, len(ids), remaining))


async def cb_page_info(cb: CallbackQuery, callback_data: PageInfo) -> None:
    await cb.answer(f"Страница {callback_data.page + 1} из {callback_data.pages}")


# --- favorites -----------------------------------------------------------------------------------------

async def _favorites(target, svc: Services, user: User, page: int = 0) -> None:
    ids = await svc.account.favorite_ids(user.id)
    pages = views.pages_of(len(ids))
    page = min(max(0, page), pages - 1)
    orders = await svc.orders.many(ids[page * PAGE:(page + 1) * PAGE])
    await show(target, views.favorites_screen(orders, page, len(ids)))


async def cb_favorites(cb: CallbackQuery, svc: Services, user: User, state: FSMContext) -> None:
    await state.set_state(None)
    await _favorites(cb, svc, user)


async def cb_fav_page(cb: CallbackQuery, callback_data: FavL, svc: Services, user: User) -> None:
    await _favorites(cb, svc, user, callback_data.page)


async def cmd_favorites(message: Message, svc: Services, user: User) -> None:
    await _favorites(message, svc, user)


# --- order card -------------------------------------------------------------------------------------------

async def late_minutes(svc: Services, user_id: int, order: dict) -> int | None:
    """For a FREE Radar card: how much earlier PRO users received this order."""
    row = await svc.db.fetchone("SELECT sent_at FROM deliveries WHERE user_id = ? AND order_id = ? AND status = 'sent'",
                                user_id, order["id"])
    if not row or not row["sent_at"]:
        return None
    return minutes_between(order["fetched_at"], parse_iso(row["sent_at"])) or None


async def card_screen(svc: Services, user: User, order: dict, src: str, ref: int, page: int) -> views.Screen:
    access = await svc.billing.access(user.id)
    free_radar = src == "r" and not access.is_pro
    late = await late_minutes(svc, user.id, order) if free_radar else None
    words = exclude_candidates(order["title"], user.excl, user.kws)
    return views.order_card(order, src, ref, page, await svc.account.is_favorite(user.id, order["id"]),
                            bool(words), late, free=not access.is_pro)


async def cb_order(cb: CallbackQuery, callback_data: Ord, svc: Services, user: User) -> None:
    user = await svc.db.get_user(user.id)
    d = callback_data
    oc = dict(id=d.id, src=d.src, ref=d.ref, page=d.page)
    order = await svc.orders.get(d.id)
    if not order:
        return await show(cb, views.order_gone(d.src, d.ref, d.page))
    if d.a == "open":
        await svc.account.opened(user.id, order["id"])
        return await show(cb, await card_screen(svc, user, order, d.src, d.ref, d.page))
    if d.a == "fav":
        added = await svc.account.toggle_favorite(user.id, order["id"])
        await cb.answer("⭐ Добавлено в избранное" if added else "Убрано из избранного")
        return await show(cb, await card_screen(svc, user, order, d.src, d.ref, d.page))
    if d.a == "tpl":
        plan = await svc.billing.plan(user.id)
        text = render_response(order, user, plan.custom_templates)
        return await show(cb, views.response_screen(order, text, oc, profile_empty(user)))
    words = exclude_candidates(order["title"], user.excl, user.kws)
    if d.a == "hide":
        if not words:
            return await cb.answer("Все слова из названия уже в исключениях", show_alert=True)
        return await show(cb, views.hide_screen(order, words, oc))
    if d.a == "hw":
        if not 0 <= d.w < len(words):
            await cb.answer("Это слово уже скрыто")
            return await show(cb, await card_screen(svc, user, order, d.src, d.ref, d.page))
        word = words[d.w]
        await svc.db.update_user(user.id, excludes=user.excl + [word])
        return await show(cb, views.hidden_ok(word, oc))
    await cb.answer("Неизвестное действие", show_alert=True)


def create_router() -> Router:
    r = Router(name="orders")
    r.message.register(cmd_search, Command("search"))
    r.message.register(cmd_favorites, Command("favorites"))
    r.callback_query.register(cb_search_home, Menu.filter(F.to == "search"))
    r.callback_query.register(cb_search, Srch.filter())
    r.message.register(msg_query, SearchInput.query, USER_TEXT)
    r.callback_query.register(cb_results, Res.filter())
    r.callback_query.register(cb_page_info, PageInfo.filter())
    r.callback_query.register(cb_favorites, Menu.filter(F.to == "favs"))
    r.callback_query.register(cb_fav_page, FavL.filter())
    r.callback_query.register(cb_order, Ord.filter())
    return r
