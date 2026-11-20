"""Feed polling, dedup, 14-day storage, Radar queue: PRO at once, FREE after 30 minutes, limits."""

from unittest.mock import patch

from radar.bot import jobs
from radar.bot.jobs import check_stale, deliver_due
from radar.utils import ago_iso, iso, now_utc
from tests.conftest import ADMIN, make_feed, onboard


def radar_cards(tg, user_id):
    return [m for m in tg.session.messages.values() if m.chat_id == user_id and "Новый заказ" in m.text]


async def test_dedup_by_link(tg, feed):
    first = await tg.svc.orders.poll_once()
    assert first.ok and first.fetched == 60 and first.new == 60
    again = await tg.svc.orders.poll_once()
    assert again.ok and again.new == 0
    assert await tg.svc.orders.count() == 60


async def test_orders_older_than_14_days_are_not_kept(tg, feed):
    feed.data = make_feed([{"id": 1, "title": "Свежий"}, {"id": 2, "title": "Старый", "minutes_ago": 15 * 24 * 60}])
    await tg.svc.orders.poll_once()
    assert await tg.svc.orders.count() == 1
    # an order that aged in the database is removed by the cleanup, unless it is in someone's favorites
    await tg.svc.db.execute("UPDATE orders SET published_at = ?, fetched_at = ?", ago_iso(days=15), ago_iso(days=15))
    feed.data = make_feed([{"id": 3, "title": "В избранном"}])
    await tg.svc.orders.poll_once()
    fav_id = await tg.svc.db.scalar("SELECT id FROM orders WHERE fl_id = 3")
    await tg.svc.db.execute("UPDATE orders SET published_at = ?, fetched_at = ? WHERE id = ?",
                            ago_iso(days=20), ago_iso(days=20), fav_id)
    await tg.send("/start")
    await tg.svc.account.toggle_favorite(tg.user["id"], fav_id)
    assert await tg.svc.orders.cleanup() == 1
    assert [r[0] for r in await tg.svc.db.fetchall("SELECT fl_id FROM orders")] == [3]


async def test_poll_errors_back_off_and_alert_admin_after_15_minutes(tg, feed):
    await tg.svc.orders.poll_once()
    feed.error = RuntimeError("HTTP 503")
    for expected in (300, 600, 1200):
        r = await tg.svc.orders.poll_once()
        assert not r.ok and "503" in r.error
        assert await tg.svc.orders.next_delay() == expected  # 150 s × 2^errors
    assert await check_stale(tg.bot, tg.svc, iso(now_utc())) is None  # fresh: no alert yet
    await tg.svc.db.kv_set("last_ok_at", ago_iso(minutes=16))
    text = await check_stale(tg.bot, tg.svc, iso(now_utc()))
    assert "нет данных 16 мин" in text and "HTTP 503" in text
    assert any(m.chat_id == ADMIN and "нет данных" in m.text for m in tg.session.messages.values())
    assert await check_stale(tg.bot, tg.svc, iso(now_utc())) is None  # once, not every poll
    feed.error = None
    assert (await tg.svc.orders.poll_once()).ok
    assert await tg.svc.orders.next_delay() == 150
    assert "снова отвечает" in await check_stale(tg.bot, tg.svc, iso(now_utc()))


async def test_bootstrap_reads_category_feeds_once(tg, feed):
    feed.data = make_feed([{"id": 1, "title": "Бот"}])
    tg.svc.orders.bootstrap_pause = 0
    assert await tg.svc.orders.bootstrap() == 1
    assert len(feed.calls) == 24 and feed.calls[1].endswith("?category=2")
    assert await tg.svc.orders.bootstrap() == 0  # not empty any more: nothing is requested
    assert len(feed.calls) == 24
    assert await tg.svc.db.scalar("SELECT COUNT(*) FROM deliveries") == 0  # history is not pushed


async def _two_users(tg, feed):
    """A FREE and a PRO user, both watching «Программирование» + «бот»."""
    feed.data = make_feed([{"id": 1, "title": "Старый бот"}])
    await tg.svc.orders.poll_once()
    free = tg
    pro = tg.as_user(200, "Про")
    await onboard(free, keywords="бот")
    await onboard(pro, keywords="бот")
    await tg.svc.billing.grant(200, 30)
    return free, pro


async def test_pro_gets_order_at_once_free_after_30_minutes(tg, feed):
    free, pro = await _two_users(tg, feed)
    feed.data = make_feed([{"id": 10, "title": "Telegram бот для записи", "budget": 15000},
                           {"id": 11, "title": "Логотип для кафе"}])
    r = await tg.svc.orders.poll_once()
    assert r.new == 2 and r.queued == 2  # one matching order × two users
    assert await deliver_due(tg.bot, tg.svc, pause=0) == 1
    (card,) = radar_cards(tg, 200)
    assert "Telegram бот для записи" in card.text and "15 000 ₽" in card.text
    assert "PRO-пользователи" not in card.text
    assert radar_cards(tg, free.user["id"]) == []  # FREE waits
    # 30 minutes later
    await tg.svc.db.execute("UPDATE orders SET fetched_at = ?", ago_iso(minutes=31))
    await tg.svc.db.execute("UPDATE deliveries SET due_at = ? WHERE user_id = ?", ago_iso(minutes=1), free.user["id"])
    assert await deliver_due(tg.bot, tg.svc, pause=0) == 1
    (card,) = radar_cards(tg, free.user["id"])
    assert "⚡ <i>PRO-пользователи получили этот заказ 31 минуту назад</i>" in card.text
    assert any(b.text == "⚡ Получать заказы мгновенно" for b in card.buttons)
    assert await deliver_due(tg.bot, tg.svc, pause=0) == 0  # never twice


async def test_free_delay_is_30_minutes_in_queue(tg, feed):
    free, pro = await _two_users(tg, feed)
    feed.data = make_feed([{"id": 10, "title": "Бот"}])
    await tg.svc.orders.poll_once()
    rows = {r["user_id"]: r for r in await tg.svc.db.fetchall("SELECT d.user_id, d.due_at, o.fetched_at FROM deliveries d "
                                                              "JOIN orders o ON o.id = d.order_id")}
    from datetime import datetime
    free_due = datetime.fromisoformat(rows[free.user["id"]]["due_at"])
    fetched = datetime.fromisoformat(rows[free.user["id"]]["fetched_at"])
    assert (free_due - fetched).total_seconds() == 30 * 60
    assert rows[200]["due_at"] == rows[200]["fetched_at"]


async def test_upgrade_releases_waiting_orders(tg, feed):
    free, _ = await _two_users(tg, feed)
    feed.data = make_feed([{"id": 10, "title": "Бот"}])
    await tg.svc.orders.poll_once()
    await tg.svc.billing.grant(free.user["id"], 7)
    await deliver_due(tg.bot, tg.svc, pause=0)
    assert len(radar_cards(tg, free.user["id"])) == 1


async def test_free_daily_limit_10_notifications(tg, feed):
    free, _ = await _two_users(tg, feed)
    feed.data = make_feed([{"id": 100 + i, "title": f"Бот {i}"} for i in range(12)])
    await tg.svc.orders.poll_once()
    await tg.svc.db.execute("UPDATE deliveries SET due_at = ?", ago_iso(minutes=1))
    await deliver_due(tg.bot, tg.svc, pause=0)
    assert len(radar_cards(tg, free.user["id"])) == 10
    assert len(radar_cards(tg, 200)) == 12  # PRO: no limit
    notes = [m for m in tg.session.messages.values() if m.chat_id == free.user["id"] and "лимит FREE" in m.text]
    assert len(notes) == 1


async def test_quiet_hours_and_radar_off(tg, feed):
    free, pro = await _two_users(tg, feed)
    await tg.svc.db.update_user(200, quiet=1)
    await tg.svc.db.update_user(free.user["id"], radar_on=0)
    feed.data = make_feed([{"id": 10, "title": "Бот"}])
    await tg.svc.orders.poll_once()
    with patch.object(jobs, "in_quiet_hours", return_value=True):
        assert await deliver_due(tg.bot, tg.svc, pause=0) == 0
    assert await tg.svc.db.scalar("SELECT status FROM deliveries WHERE user_id = 200") == "quiet"
    assert await tg.svc.db.scalar("SELECT COUNT(*) FROM deliveries WHERE user_id = ?", free.user["id"]) == 0


async def test_old_orders_found_after_downtime_are_not_pushed(tg, feed):
    await _two_users(tg, feed)
    feed.data = make_feed([{"id": 50, "title": "Бот вчерашний", "minutes_ago": 5 * 60}])
    r = await tg.svc.orders.poll_once()
    assert r.new == 1 and r.queued == 0


async def test_radar_card_buttons_work(tg, feed):
    _, pro = await _two_users(tg, feed)
    feed.data = make_feed([{"id": 10, "title": "Telegram бот"}])
    await tg.svc.orders.poll_once()
    await deliver_due(tg.bot, tg.svc, pause=0)
    card = await pro.press("⭐ В избранное")
    assert "✅ В избранном" in [b.text for b in card.buttons]
    tpl = await pro.press("Шаблон отклика")
    assert any(b.copy_text for b in tpl.buttons)
    menu = await pro.press("Меню")
    assert any(b.text.startswith("💎 PRO до") for b in menu.buttons)
