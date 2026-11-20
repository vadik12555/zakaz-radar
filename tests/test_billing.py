"""Telegram Stars payments (idempotent), admin with PRO forever, admin commands."""

from radar.devtools.fake_telegram import TelegramSimulator
from tests.conftest import ADMIN, onboard


async def _buy(tg, label="PRO месяц"):
    await tg.send("/pro")
    await tg.press(label)
    return tg.session.invoices[-1]


async def test_invoice_and_successful_payment(tg):
    await tg.svc.orders.poll_once()
    await onboard(tg)
    inv = await _buy(tg)
    assert inv.currency == "XTR" and inv.prices[0].amount == 149
    await tg.pre_checkout(inv.payload, 149)
    assert tg.session.pre_checkout_answers[-1].ok
    msg = await tg.pay(inv.payload, 149, "charge-1")
    assert "PRO месяц активирован" in msg.text
    access = await tg.svc.billing.access(tg.user["id"])
    assert access.is_pro and access.plan.code == "pro"
    assert await tg.svc.usage.remaining(tg.user["id"]) is None  # unlimited search
    # admins are told about the payment
    assert any(m.chat_id == ADMIN and "Оплата" in m.text for m in tg.session.messages.values())
    menu = await tg.send("/start")
    assert menu.buttons[0].text == "🔎 Поиск заказов"
    assert not any("мгновенно" in b.text for b in menu.buttons)


async def test_same_charge_is_credited_once(tg):
    await tg.send("/start")
    inv = await _buy(tg, "PRO неделя")
    await tg.pay(inv.payload, 49, "charge-dup")
    first = (await tg.svc.billing.access(tg.user["id"])).ends_at
    msg = await tg.pay(inv.payload, 49, "charge-dup")  # Telegram re-delivers the same update
    assert "уже зачислен" in msg.text
    assert (await tg.svc.billing.access(tg.user["id"])).ends_at == first
    assert await tg.svc.db.scalar("SELECT COUNT(*) FROM subscriptions") == 1


async def test_second_purchase_adds_days(tg):
    await tg.send("/start")
    inv = await _buy(tg, "PRO неделя")
    await tg.pay(inv.payload, 49, "c1")
    inv = await _buy(tg, "PRO месяц")
    await tg.pay(inv.payload, 149, "c2")
    from datetime import datetime, timezone
    ends = datetime.fromisoformat((await tg.svc.billing.access(tg.user["id"])).ends_at)
    days = (ends - datetime.now(timezone.utc)).days
    assert 36 <= days <= 37


async def test_pre_checkout_rejects_wrong_amount_and_foreign_invoice(tg):
    await tg.send("/start")
    inv = await _buy(tg)
    await tg.pre_checkout(inv.payload, 1)
    assert not tg.session.pre_checkout_answers[-1].ok
    other = tg.as_user(300)
    await other.send("/start")
    await other.pre_checkout(inv.payload, 149)
    assert not tg.session.pre_checkout_answers[-1].ok
    await other.pay(inv.payload, 149, "stolen")
    assert not (await tg.svc.billing.access(300)).is_pro


async def test_payments_disabled_hides_buttons(settings, feed):
    sim = await TelegramSimulator.create(settings.__class__(**{**settings.__dict__, "payments_enabled": False}),
                                         fetch=feed)
    try:
        await sim.send("/start")
        pro = await sim.send("/pro")
        assert not any(b.text.startswith("⭐") for b in pro.buttons) and "временно недоступна" in pro.text
    finally:
        await sim.close()


async def test_admin_has_pro_forever_without_payment(tg):
    admin = tg.as_user(ADMIN, "Админ")
    await admin.svc.orders.poll_once()
    await onboard(admin)
    access = await admin.svc.billing.access(ADMIN)
    assert access.is_admin and access.is_pro and access.ends_at is None and access.plan.code == "pro"
    menu = await admin.press("Меню")
    labels = [b.text for b in menu.buttons]
    assert "💎 PRO · навсегда (админ)" in labels and "🔎 Поиск заказов" in labels
    assert "PRO · навсегда (админ)" in menu.text
    pro = await admin.press("PRO · навсегда")
    assert "без оплаты" in pro.text and not admin.session.invoices
    assert await admin.svc.usage.remaining(ADMIN) is None
    assert await admin.svc.db.scalar("SELECT COUNT(*) FROM subscriptions") == 0


async def test_admin_panel_and_commands(tg):
    await tg.svc.orders.poll_once()
    await onboard(tg)
    admin = tg.as_user(ADMIN, "Админ")
    panel = await admin.send("/admin")
    assert "Админка" in panel.text and "Новых заказов за сутки: 60" in panel.text
    assert "Воронка" in panel.text and "/start: 1" in panel.text and "последний успешный опрос 0 мин назад" in panel.text
    refreshed = await admin.press("Обновить")
    assert "Админка" in refreshed.text
    msg = await admin.send(f"/grant {tg.user['id']} 10")
    assert "Выдан PRO" in msg.text
    assert (await tg.svc.billing.access(tg.user["id"])).is_pro
    assert any(m.chat_id == tg.user["id"] and "Тебе выдан" in m.text for m in tg.session.messages.values())
    card = await admin.send(f"/user {tg.user['id']}")
    assert "PRO до" in card.text and "Программирование" in card.text
    preview = await admin.send("/broadcast Привет всем!")
    assert "Рассылка" in preview.text and "✅ Отправить всем" in [b.text for b in preview.buttons]
    cancelled = await admin.press("Отмена")
    assert "отменена" in cancelled.text
    # a regular user can't open the admin panel
    other = await tg.send("/admin")
    assert "Админка" not in other.text


async def test_refund(tg):
    await tg.send("/start")
    inv = await _buy(tg)
    await tg.pay(inv.payload, 149, "charge-r")
    admin = tg.as_user(ADMIN)
    msg = await admin.send("/refund charge-r")
    assert "Возврат выполнен" in msg.text and tg.session.refunds
    assert not (await tg.svc.billing.access(tg.user["id"])).is_pro
    assert "уже возвращён" in (await admin.send("/refund charge-r")).text


async def test_support_roundtrip(tg):
    await tg.send("/start")
    await tg.send("/support")
    done = await tg.send("Не приходят заказы")
    assert "отправлено" in done.text
    note = next(m for m in tg.session.messages.values() if m.chat_id == ADMIN and "#u" in m.text)
    admin = tg.as_user(ADMIN)
    await admin.send("Проверим!", reply_to=note)
    assert any(m.chat_id == tg.user["id"] and "Ответ поддержки" in m.text for m in tg.session.messages.values())
