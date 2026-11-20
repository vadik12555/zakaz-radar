"""PRO screen and Telegram Stars payments (one-time invoices, credited once per charge id)."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

from radar.bot import views
from radar.bot.callbacks import Menu, Pay
from radar.bot.ui import show
from radar.db import User
from radar.services import Services
from radar.services.billing import CURRENCY
from radar.utils import fmt_date

log = logging.getLogger(__name__)


async def _pro(target, svc: Services, user: User) -> None:
    access = await svc.billing.access(user.id)
    await show(target, views.pro_screen(access, svc.settings.product.for_sale, svc.settings.payments_enabled,
                                        svc.settings.product.free))


async def cb_pro(cb: CallbackQuery, svc: Services, user: User, state: FSMContext) -> None:
    await state.set_state(None)
    await _pro(cb, svc, user)


async def cmd_pro(message: Message, svc: Services, user: User) -> None:
    await _pro(message, svc, user)


async def cb_pay(cb: CallbackQuery, callback_data: Pay, svc: Services, user: User, bot: Bot) -> None:
    if not svc.settings.payments_enabled:
        return await cb.answer("Оплата временно недоступна. Напиши в /support", show_alert=True)
    try:
        plan, payload = await svc.billing.create_invoice(user.id, callback_data.plan)
    except ValueError:
        return await cb.answer("Этот тариф недоступен", show_alert=True)
    await svc.account.event(user.id, "pay")
    title, description = views.invoice_texts(plan)
    await bot.send_invoice(chat_id=cb.from_user.id, title=title, description=description, payload=payload,
                           currency=CURRENCY, prices=[LabeledPrice(label=title, amount=plan.price_stars)])
    await cb.answer("Счёт ниже 👇 Оплата звёздами Telegram")


async def on_pre_checkout(query: PreCheckoutQuery, svc: Services) -> None:
    ok, error = await svc.billing.validate_pre_checkout(query.from_user.id, query.invoice_payload,
                                                        query.currency, query.total_amount)
    await query.answer(ok=ok, error_message=error)


async def on_successful_payment(message: Message, svc: Services) -> None:
    sp = message.successful_payment
    result = await svc.billing.confirm_payment(
        user_id=message.from_user.id, payload=sp.invoice_payload, charge_id=sp.telegram_payment_charge_id,
        provider_charge_id=sp.provider_payment_charge_id, currency=sp.currency, amount=sp.total_amount)
    log.info("payment %s from user %s: %s", sp.telegram_payment_charge_id, message.from_user.id, result.status)
    if result.status == "activated":
        await svc.account.event(message.from_user.id, "paid")
        await show(message, views.payment_ok(result.plan, result.ends_at))
        u = message.from_user
        who = f"@{views.h(u.username)}" if u.username else views.h(u.full_name)
        await _notify_admins(message.bot, svc, f"💰 <b>Оплата</b>: {result.plan.title} · {sp.total_amount} ⭐\n"
                                               f"От: {who} (id {u.id})\nДо: {fmt_date(result.ends_at)}\n"
                                               f"charge: <code>{views.h(sp.telegram_payment_charge_id)}</code>")
    elif result.status == "duplicate":
        await message.answer("Этот платёж уже зачислен ✅")
    else:
        log.error("payment needs manual review: %s (%s)", sp.telegram_payment_charge_id, result.status)
        await message.answer(views.payment_problem(svc.settings.support_contact))


async def _notify_admins(bot: Bot, svc: Services, text: str) -> None:
    for admin in svc.settings.admin_ids:
        try:
            await bot.send_message(admin, text)
        except Exception as e:
            log.warning("payment notice to admin %s failed: %s", admin, e)


def create_router() -> Router:
    r = Router(name="billing")
    r.callback_query.register(cb_pro, Menu.filter(F.to == "pro"))
    r.message.register(cmd_pro, Command("pro"))
    r.callback_query.register(cb_pay, Pay.filter())
    r.pre_checkout_query.register(on_pre_checkout)
    r.message.register(on_successful_payment, F.successful_payment)
    return r
