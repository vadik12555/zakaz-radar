"""Admin panel. Only Telegram ids from ADMIN_IDS reach these handlers."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import BaseFilter, Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from radar.bot import views
from radar.bot.callbacks import Adm
from radar.bot.ui import show
from radar.fl import category_label
from radar.services import Services
from radar.utils import fmt_date

log = logging.getLogger(__name__)


class IsAdmin(BaseFilter):
    async def __call__(self, event, is_admin: bool = False) -> bool:
        return is_admin


async def admin_screen(svc: Services) -> views.Screen:
    ov = await svc.account.overview()
    ov["pro"] = await svc.billing.active_pro_users()
    status = await svc.orders.status()
    return views.admin_screen(ov, status, svc.orders.stale_minutes(status.get("last_ok_at")),
                              svc.settings.poll_seconds)


async def cmd_admin(message: Message, svc: Services) -> None:
    await show(message, await admin_screen(svc))


async def cb_admin(cb: CallbackQuery, callback_data: Adm, svc: Services, state: FSMContext, bot: Bot) -> None:
    act = callback_data.act
    if act == "home":
        await cb.answer("Обновлено")
        return await show(cb, await admin_screen(svc))
    if act in ("bc_ok", "bc_no"):
        text = (await state.get_data()).get("broadcast")
        await state.update_data(broadcast=None)
        back = views.kb([views.b("← Админка", Adm(act="home"))])
        if act == "bc_no" or not text:
            return await show(cb, ("Рассылка отменена." if act == "bc_no" else "Рассылка уже отправлена или устарела.",
                                   back))
        asyncio.create_task(broadcast(bot, svc, cb.from_user.id, text))
        return await show(cb, ("📣 Рассылка запущена. Пришлю итог, когда закончу.", back))


async def cmd_grant(message: Message, command: CommandObject, svc: Services, bot: Bot) -> None:
    parts = (command.args or "").split()
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit() or int(parts[1]) <= 0:
        return await message.answer("Использование: /grant TELEGRAM_ID ДНЕЙ — выдать PRO без оплаты")
    user_id, days = int(parts[0]), int(parts[1])
    if not await svc.db.get_user(user_id):
        return await message.answer("Пользователь ещё не запускал бота.")
    ends = await svc.billing.grant(user_id, days)
    log.info("admin %s granted PRO for %s days to %s", message.from_user.id, days, user_id)
    await message.answer(f"✅ Выдан PRO до {fmt_date(ends)} пользователю {user_id}")
    try:
        await bot.send_message(user_id, f"🎁 Тебе выдан 💎 PRO до {fmt_date(ends)}. Радар теперь без задержки!",
                               reply_markup=views.kb([views.home_btn()]))
    except Exception as e:
        log.info("grant notice to %s failed: %s", user_id, type(e).__name__)


async def cmd_refund(message: Message, command: CommandObject, svc: Services, bot: Bot) -> None:
    charge_id = (command.args or "").strip()
    payment = await svc.billing.payment_by_charge(charge_id) if charge_id else None
    if not payment:
        return await message.answer("Использование: /refund TELEGRAM_CHARGE_ID (есть в уведомлении об оплате и в /user)")
    if payment["status"] == "refunded":
        return await message.answer("Этот платёж уже возвращён.")
    try:
        await bot.refund_star_payment(user_id=payment["user_id"], telegram_payment_charge_id=charge_id)
    except Exception as e:
        log.error("refund failed for %s: %s", charge_id, e)
        return await message.answer(f"Telegram не выполнил возврат: {views.h(str(e))}")
    await svc.billing.mark_refunded(charge_id)
    log.info("admin %s refunded %s", message.from_user.id, charge_id)
    await message.answer("✅ Возврат выполнен, PRO по этому платежу отключён.")


async def cmd_user(message: Message, command: CommandObject, svc: Services) -> None:
    arg = (command.args or "").strip()
    if not arg.isdigit():
        return await message.answer("Использование: /user TELEGRAM_ID")
    card = await svc.account.user_card(int(arg))
    if not card:
        return await message.answer("Пользователь не найден.")
    access = await svc.billing.access(card["id"])
    pays = "\n".join(f"  {p['plan']} · {p['amount']} ⭐ · {p['status']} · <code>{views.h(p['telegram_charge_id'] or '-')}</code>"
                     for p in card["payments"]) or "  нет"
    who = f"@{views.h(card['username'])}" if card["username"] else views.h(card["first_name"] or "—")
    await message.answer(
        f"👤 <b>{card['id']}</b> {who}\nС нами с {fmt_date(card['created_at'])}, был {fmt_date(card['last_seen_at'])}"
        f"{' · заблокировал бота' if card['blocked'] else ''}\n"
        f"{views.plan_line(access, None)}\n"
        f"Онбординг: {'да' if card['onboarded'] else 'нет'} · Радар: {'вкл' if card['radar_on'] else 'выкл'}\n"
        f"Категории: {views.h(', '.join(category_label(c) for c in card['categories']) or '—')}\n"
        f"Слова: {views.h(', '.join(card['keywords']) or '—')} · Исключения: {views.h(', '.join(card['excludes']) or '—')}\n"
        f"Поисков (списано): {card['searches']} · Избранное: {card['favorites']} · Уведомлений: {card['sent']}\n"
        f"Платежи:\n{pays}")


async def cmd_broadcast(message: Message, command: CommandObject, state: FSMContext, svc: Services) -> None:
    text = (command.args or "").strip()
    if not text:
        return await message.answer("Использование: /broadcast ТЕКСТ (HTML можно). Перед отправкой будет подтверждение.")
    await state.update_data(broadcast=text)
    await show(message, views.broadcast_preview(text, len(await svc.account.broadcast_targets())))


async def broadcast(bot: Bot, svc: Services, admin_id: int, text: str) -> None:
    sent = failed = 0
    for uid in await svc.account.broadcast_targets():
        try:
            await bot.send_message(uid, text, reply_markup=views.kb([views.home_btn()]))
            sent += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramForbiddenError:
            await svc.db.update_user(uid, blocked=1)
            failed += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    try:
        await bot.send_message(admin_id, f"📣 Рассылка завершена: доставлено {sent}, не доставлено {failed}.")
    except Exception:
        pass


def create_router() -> Router:
    r = Router(name="admin")
    r.message.filter(IsAdmin())
    r.callback_query.filter(IsAdmin())
    r.message.register(cmd_admin, Command("admin"))
    r.message.register(cmd_grant, Command("grant"))
    r.message.register(cmd_refund, Command("refund"))
    r.message.register(cmd_user, Command("user"))
    r.message.register(cmd_broadcast, Command("broadcast"))
    r.callback_query.register(cb_admin, Adm.filter())
    return r


