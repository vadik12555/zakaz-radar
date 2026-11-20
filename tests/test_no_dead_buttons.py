"""NO DEAD BUTTONS: walk the bot as a user and press every inline button (except url/copy) on every screen.

A press passes when the bot visibly answers: edits the screen, sends a message or an invoice, or shows
a callback alert with text. It fails on: no answer at all, an error alert ("что-то пошло не так"),
a stale-button fallback, or any ERROR in the logs. Run for FREE, for PRO and for the admin.
"""

import asyncio
import logging
from datetime import datetime, timezone

import pytest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendInvoice, SendMessage

from radar.bot.jobs import deliver_due
from radar.devtools.fake_telegram import BOT_USER
from radar.utils import ago_iso
from tests.conftest import ADMIN, make_feed, shifted_sample

MAX_PRESSES = 800
BAD_ALERTS = ("пошло не так", "устарела")


class ErrorLog(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.records: list[str] = []

    def emit(self, record):
        self.records.append(f"{record.name}: {record.getMessage()}")


@pytest.fixture
def errors():
    h = ErrorLog()
    logging.getLogger().addHandler(h)
    yield h.records
    logging.getLogger().removeHandler(h)


async def _press(tg, msg, btn):
    await tg._feed({
        "update_id": next(tg._update_ids),
        "callback_query": {
            "id": str(next(tg._update_ids)), "from": tg.user, "chat_instance": "sim", "data": btn.callback_data,
            "message": {"message_id": msg.message_id, "date": int(datetime.now(timezone.utc).timestamp()),
                        "chat": {"id": msg.chat_id, "type": "private"},
                        "from": BOT_USER.model_dump(exclude_none=True), "text": msg.text}}})


def _snapshot(tg):
    return {m.message_id: (m.text, str(m.markup)) for m in tg._mine()}


def _visible_answer(requests) -> bool:
    for r in requests:
        if isinstance(r, (SendMessage, EditMessageText, SendInvoice)):
            return True
        if isinstance(r, AnswerCallbackQuery) and r.text:
            return True
    return False


async def crawl(tg, start, errors: list[str], skip: tuple[str, ...] = ()) -> tuple[list[str], set[str]]:
    """Depth-first walk from a screen. Returns (broken buttons, labels pressed).
    A button with the same callback_data as an already pressed one does the same thing: its label counts
    as pressed. `skip` labels are left for another walk (e.g. «Сбросить» before «Далее» in onboarding)."""
    broken, seen, labels = [], set(), set()

    async def walk(msg, path):
        for btn in list(msg.buttons):
            if btn.url:
                assert btn.url.startswith("https://www.fl.ru/"), btn.url
                continue
            if btn.copy_text:
                assert 0 < len(btn.copy_text.text) <= 256
                continue
            if any(word in btn.text for word in skip):
                continue
            if btn.callback_data in seen:
                labels.add(btn.text)
                continue
            if len(seen) >= MAX_PRESSES:
                continue
            seen.add(btn.callback_data)
            labels.add(btn.text)
            before = _snapshot(tg)
            n_req, n_alerts, n_err = len(tg.session.requests), len(tg.session.alerts), len(errors)
            await _press(tg, msg, btn)
            label = " > ".join(path + [btn.text])
            new_alerts = tg.session.alerts[n_alerts:]
            if not _visible_answer(tg.session.requests[n_req:]):
                broken.append(f"{label}: no answer")
            if any(bad in a for a in new_alerts for bad in BAD_ALERTS):
                broken.append(f"{label}: {new_alerts}")
            if len(errors) > n_err:
                broken.append(f"{label}: errors {errors[n_err:]}")
            changed = [m for m in tg._mine() if _snapshot(tg).get(m.message_id) != before.get(m.message_id)]
            for m in changed:
                await walk(m, path + [btn.text])

    await walk(start, [])
    return broken, labels


async def _radar_card(tg, user_id):
    """Push one fresh matching order to the user through the real delivery job."""
    tg.svc.orders.fetch = None
    feed = make_feed([{"id": 9_000_001, "title": "Telegram бот для записи клиентов", "budget": 15000,
                       "desc": "Нужен бот на aiogram с оплатой"}])

    async def fetch(url):
        return feed
    tg.svc.orders.fetch = fetch
    await tg.svc.orders.poll_once()
    await tg.svc.db.execute("UPDATE orders SET fetched_at = ? WHERE fl_id = 9000001", ago_iso(minutes=31))
    await tg.svc.db.execute("UPDATE deliveries SET due_at = ?", ago_iso(minutes=1))
    assert await deliver_due(tg.bot, tg.svc, pause=0) >= 1
    return next(m for m in reversed(tg._mine()) if "Новый заказ" in m.text)


def _report(name, broken, labels):
    print(f"\n{name}: pressed {len(labels)} different buttons\nBROKEN: {broken}")


async def test_no_dead_buttons_free(tg, errors):
    await tg.svc.orders.poll_once()  # the real saved FL.ru feed (60 orders)
    broken, labels = await crawl(tg, await tg.send("/start"), errors)  # onboarding → everything reachable
    user = await tg.svc.db.get_user(tg.user["id"])
    assert user.onboarded
    await tg.svc.db.update_user(tg.user["id"], categories=[5], keywords=["бот"], excludes=[], radar_on=1, quiet=0)
    for start in (await tg.send("/start"), await _radar_card(tg, tg.user["id"])):
        b2, l2 = await crawl(tg, start, errors)
        broken, labels = broken + b2, labels | l2
    # free searches used up: the paywall and its payment buttons
    for i in range(3):
        await tg.svc.usage.charge(tg.user["id"], f"test-{i}")
    b3, l3 = await crawl(tg, await tg.send("/start"), errors)
    broken, labels = broken + b3, labels | l3
    _report("FREE", broken, labels)
    assert not broken
    must = ["Далее ➡️", "Пропустить ➡️", "Любой", "🎁 3 бесплатных поиска", "🎯 По моим фильтрам", "✏️ Свой запрос",
            "✍️ Шаблон отклика", "⭐ В избранное", "🙈 Скрыть похожие", "📡 Радар: вкл ✅", "⚙️ Фильтры",
            "🔒 💰 Бюджет", "🌙 Тихие часы: выкл", "👤 Профиль", "✏️ Имя", "🔒 Свой шаблон отклика", "💎 PRO",
            "⭐ PRO неделя — 49 ⭐", "⭐ PRO месяц — 149 ⭐", "⚡ Получать заказы мгновенно", "📊 Статистика",
            "❓ Помощь", "💬 Написать в поддержку", "📄 Условия", "🔒 Конфиденциальность", "🔎 Поиск заказов"]
    missing = [m for m in must if m not in labels]
    assert not missing, missing
    assert tg.session.invoices  # payment buttons really send invoices


async def test_no_dead_buttons_pro(tg, errors):
    await tg.svc.orders.poll_once()
    await tg.send("/start")
    await tg.svc.billing.grant(tg.user["id"], 30)
    broken, labels = await crawl(tg, await tg.send("/start"), errors, skip=("Сбросить",))  # onboarding as PRO
    assert (await tg.svc.db.get_user(tg.user["id"])).onboarded
    await tg.svc.db.update_user(tg.user["id"], onboarded=0)  # onboarding once more: now «Сбросить» too
    b1, l1 = await crawl(tg, await tg.send("/start"), errors)
    broken, labels = broken + b1, labels | l1
    await tg.svc.db.update_user(tg.user["id"], categories=[5, 34, 31], keywords=["бот", "telegram"], onboarded=1,
                                template="Сделаю «{заказ}». {имя}", p_name="Иван", radar_on=1, excludes=[], quiet=0,
                                min_budget=0, show_no_budget=1)  # the walk changed filters: reset them
    for start in (await tg.send("/start"), await _radar_card(tg, tg.user["id"])):
        b2, l2 = await crawl(tg, start, errors)
        broken, labels = broken + b2, labels | l2
    _report("PRO", broken, labels)
    assert not broken
    must = ["🔎 Поиск заказов", "🌐 Выбрать все", "🧹 Сбросить", "💰 Бюджет", "от 30 000 ₽", "Только с бюджетом",
            "✍️ Мой шаблон отклика", "✏️ Изменить", "↩️ Вернуть стандартный", "✍️ Шаблон отклика", "🙈 Скрыть похожие"]
    missing = [m for m in must if m not in labels]
    assert not missing, missing
    trial = [t for t in labels if t.startswith("🎁") or "мгновенно" in t]  # FREE-only buttons
    assert not trial, trial


async def test_no_dead_buttons_admin(tg, errors):
    admin = tg.as_user(ADMIN, "Админ")
    await admin.svc.orders.poll_once()
    broken, labels = await crawl(admin, await admin.send("/start"), errors, skip=("Сбросить",))
    for cmd in ("/start", "/admin", "/broadcast Тест рассылки"):
        b2, l2 = await crawl(admin, await admin.send(cmd), errors)
        broken, labels = broken + b2, labels | l2
    await asyncio.sleep(0.3)  # let the broadcast task finish
    _report("ADMIN", broken, labels)
    assert not broken
    assert {"💎 PRO · навсегда (админ)", "🔄 Обновить", "✅ Отправить всем"} <= labels


async def test_old_buttons_after_restart_still_answer(tg, errors):
    """Buttons from old messages (order deleted after 14 days, search cleaned up, unknown data) still answer."""
    from aiogram.types import InlineKeyboardButton

    from radar.bot.callbacks import Ord, Res
    await tg.svc.orders.poll_once()
    await tg.send("/start")
    await tg.svc.db.update_user(tg.user["id"], onboarded=1, categories=[5])
    msg = await tg.send("/start")
    expected = {Ord(id=10**9, src="s", ref=5, page=0).pack(): "уже нет в базе",
                Res(sid=10**9).pack(): "устарели",
                "garbage:1": "ЗаказРадар"}
    for data, text in expected.items():
        n_req = len(tg.session.requests)
        await _press(tg, msg, InlineKeyboardButton(text="old", callback_data=data))
        assert _visible_answer(tg.session.requests[n_req:]), data
        assert text in tg.last.text, (data, tg.last.text)
    assert not errors
