"""User scenarios through real Telegram updates (simulator): onboarding, search limit, card actions."""

from aiogram.enums import ButtonStyle

from tests.conftest import buttons, make_feed, onboard


async def test_onboarding_shows_three_fresh_orders(tg):
    await tg.svc.orders.poll_once()
    first = await tg.send("/start")
    assert "Шаг 1 из 3" in first.text
    msg = await tg.press("Программирование")
    assert "✅ 💻 Программирование" in buttons(msg)
    msg = await tg.press("Дизайн")  # without a subscription filters are not limited
    assert "✅ 🎨 Дизайн" in buttons(msg) and "✅ 💻 Программирование" in buttons(msg)
    msg = await tg.press("Далее")
    assert "Шаг 2 из 3" in msg.text
    msg = await tg.send("логотип, иллюстрация, баннер, визитка")
    assert "Шаг 3 из 3" in msg.text and "FREE" not in msg.text
    done = await tg.press("от 10 000")
    assert "Готово" in done.text and "Радар — новые заказы сами в чат — работает по подписке" in done.text
    assert "3 поиска бесплатно" in done.text
    assert any(b.text.startswith("⚡ Подключить Радар") and b.style == ButtonStyle.SUCCESS for b in done.buttons)
    user = await tg.svc.db.get_user(tg.user["id"])
    assert user.onboarded and user.radar_on and sorted(user.cats) == [3, 5] and len(user.kws) == 4
    assert user.min_budget == 10000
    assert await tg.svc.db.scalar("SELECT COUNT(*) FROM events WHERE name IN ('start', 'filters')") == 2


async def test_onboarding_preview_has_up_to_three_cards(tg, feed):
    feed.data = make_feed([{"id": i, "title": f"Telegram бот №{i}", "minutes_ago": i} for i in range(1, 6)])
    await tg.svc.orders.poll_once()
    done = await onboard(tg, keywords="бот")
    order_buttons = [b for b in done.buttons if b.callback_data and b.callback_data.startswith("o:")]
    assert len(order_buttons) == 3 and "Всего подходящих за 14 дней: <b>5</b>" in done.text
    card = await tg.press("1. Telegram бот №1")
    assert "Telegram бот №1" in card.text and "бюджет не указан" in card.text
    assert "опубликован 1 минуту назад" in card.text


async def test_free_menu_buttons_and_colors(tg):
    await tg.svc.orders.poll_once()
    menu = await onboard(tg)
    menu = await tg.press("Меню")
    first = menu.buttons[0]
    assert first.text == "🎁 3 поиска бесплатно" and first.style == ButtonStyle.DANGER
    assert "FREE" not in menu.text and "на всё время, не в день" in menu.text
    by_text = {b.text: b for b in menu.buttons}
    assert by_text["⚙️ Фильтры"].style == ButtonStyle.PRIMARY
    assert by_text["📡 Радар: 🔒 по подписке"].style == ButtonStyle.PRIMARY
    assert by_text["⚡ Подключить Радар — от 49 ⭐"].style == ButtonStyle.SUCCESS
    assert by_text["❓ Помощь"].style is None
    pro = await tg.press("💎 Подписка")
    assert "FREE" not in pro.text and "на всё время (не в день)" in pro.text
    assert "PRO год — 990 ⭐ (≈ 81 ⭐ в месяц)" in pro.text
    assert all(b.style == ButtonStyle.SUCCESS for b in pro.buttons if b.text.startswith("⭐"))
    assert [b.text for b in pro.buttons if b.text.startswith("⭐")] == [
        "⭐ PRO неделя — 49 ⭐", "⭐ PRO месяц — 149 ⭐", "⭐ PRO год — 990 ⭐"]


async def test_radar_switch(tg):
    await tg.svc.orders.poll_once()
    await onboard(tg)
    menu = await tg.press("Меню")
    pro = await tg.press("📡 Радар: 🔒 по подписке")  # without a subscription: the subscription screen
    assert "Подписка PRO" in pro.text and "по подписке" in tg.session.alerts[-1]
    await tg.svc.billing.grant(tg.user["id"], 7)
    menu = await tg.press("Меню")
    menu = await tg.press("📡 Радар: вкл")
    assert "📡 Радар: выкл" in buttons(menu)
    assert not (await tg.svc.db.get_user(tg.user["id"])).radar_on
    menu = await tg.press("📡 Радар: выкл")
    assert "📡 Радар: вкл ✅" in buttons(menu)


async def test_three_free_searches_idempotent_then_paywall(tg, feed):
    feed.data = make_feed([{"id": i, "title": f"Бот {w}", "minutes_ago": i}
                           for i, w in enumerate(["альфа", "бета", "гамма", "дельта", "эпсилон"], 1)])
    await tg.svc.orders.poll_once()
    await onboard(tg)
    await tg.press("Меню")
    await tg.press("поиска бесплатно")
    res = await tg.press("По моим фильтрам")
    assert "Найдено: 5" in res.text and "Бесплатных поисков осталось: 2" in res.text
    await tg.press("Новый поиск")
    res = await tg.press("По моим фильтрам")  # the same search again: not charged
    assert "Бесплатных поисков осталось: 2" in res.text
    await tg.press("Новый поиск")
    await tg.press("Свой запрос")
    empty = await tg.send("квантовый компьютер")  # nothing found: not charged
    assert "ничего не нашлось" in empty.text and "не списан" in empty.text
    assert await tg.svc.usage.remaining(tg.user["id"]) == 2
    await tg.press("Свой запрос")
    res = await tg.send("альфа")
    assert "Найдено: 1" in res.text and "осталось: 1" in res.text
    await tg.press("Новый поиск")
    await tg.press("Свой запрос")
    res = await tg.send("бета")
    assert "осталось: 0" in res.text
    wall = await tg.press("Новый поиск")
    assert "Бесплатные поиски закончились" in wall.text
    assert "нашлось <b>5</b> заказов" in wall.text  # a week of orders under the user's filters
    assert any(b.text.startswith("⭐ PRO") and b.style == ButtonStyle.SUCCESS for b in wall.buttons)
    menu = await tg.send("/start")
    assert menu.buttons[0].text == "🔎 Поиск заказов"  # the red trial button is gone
    # the results already bought stay readable
    assert await tg.svc.db.scalar("SELECT COUNT(*) FROM usage_events") == 3


async def test_results_paging_and_card_actions(tg, feed):
    feed.data = make_feed([{"id": i, "title": f"Telegram бот для магазина {i}", "budget": 5000 * i,
                            "minutes_ago": i} for i in range(1, 9)])
    await tg.svc.orders.poll_once()
    await onboard(tg, keywords="бот")
    await tg.press("Меню")
    await tg.press("поиска бесплатно")
    res = await tg.press("По моим фильтрам")
    assert "Найдено: 8" in res.text and "1 / 2" in buttons(res)
    alerts = len(tg.session.alerts)
    await tg.press("1 / 2")
    assert tg.session.alerts[alerts] == "Страница 1 из 2"
    page2 = await tg.press("›")
    assert "7. " in page2.text
    card = await tg.press("7. Telegram")
    assert "💰 35 000 ₽" in card.text
    assert card.buttons[0].url.startswith("https://www.fl.ru/projects/")
    card = await tg.press("⭐ В избранное")
    assert "✅ В избранном" in buttons(card)
    tpl = await tg.press("Шаблон отклика")
    copy = next(b for b in tpl.buttons if b.copy_text)
    assert "Telegram бот для магазина 7" in copy.copy_text.text and len(copy.copy_text.text) <= 256
    assert "Профиль" in tpl.text  # profile is empty: hint
    await tg.press("К заказу")
    hide = await tg.press("Скрыть похожие")
    assert "Выбери слово" in hide.text
    ok = await tg.press("магаз")
    assert "добавлено в исключения" in ok.text
    user = await tg.svc.db.get_user(tg.user["id"])
    assert any(w.startswith("магаз") for w in user.excl)
    back = await tg.press("К заказу")
    assert "Telegram бот для магазина 7" in back.text
    favs = await tg.send("/favorites")
    assert "Избранное</b> (1)" in favs.text


async def test_profile_goes_into_response(tg, feed):
    feed.data = make_feed([{"id": 1, "title": "Парсер Avito"}])
    await tg.svc.orders.poll_once()
    await onboard(tg)
    await tg.send("/profile")
    await tg.press("✏️ Имя")
    await tg.send("Иван")
    await tg.press("✏️ Специализация")
    await tg.send("Python-разработчик")
    await tg.press("✏️ Опыт")
    await tg.send("5 лет")
    prof = await tg.press("✏️ Портфолио")
    prof = await tg.send("https://github.com/ivan")
    assert "Иван" in prof.text and "github.com/ivan" in prof.text
    await tg.send("/start")
    await tg.press("поиска бесплатно")
    await tg.press("По моим фильтрам")
    await tg.press("1. Парсер Avito")
    tpl = await tg.press("Шаблон отклика")
    text = next(b for b in tpl.buttons if b.copy_text).copy_text.text
    assert text.startswith("Здравствуйте! Готов выполнить заказ «Парсер Avito».")
    assert "Python-разработчик, опыт 5 лет" in text and "github.com/ivan" in text and text.endswith("Иван")


async def test_pro_custom_template(tg, feed):
    feed.data = make_feed([{"id": 1, "title": "Лендинг", "budget": 12000}])
    await tg.svc.orders.poll_once()
    await onboard(tg)
    await tg.svc.billing.grant(tg.user["id"], 30)
    await tg.send("/profile")
    await tg.press("Мой шаблон отклика")
    await tg.press("Изменить")
    saved = await tg.send("Сделаю «{заказ}» за {бюджет}!")
    assert "свой" in saved.text
    await tg.send("/search")
    await tg.press("По моим фильтрам")
    await tg.press("1. Лендинг")
    tpl = await tg.press("Шаблон отклика")
    assert next(b for b in tpl.buttons if b.copy_text).copy_text.text == "Сделаю «Лендинг» за 12 000 ₽!"


async def test_filters_screen_actions(tg):
    await tg.svc.orders.poll_once()
    await onboard(tg)
    flt = await tg.send("/filters")
    assert "Фильтры" in flt.text
    await tg.press("Исключения")
    flt = await tg.send("курсовая, диплом")
    assert "курсовая, диплом" in flt.text
    flt = await tg.press("Тихие часы: выкл")
    assert "🌙 Тихие часы: вкл" in buttons(flt)
    flt = await tg.press("Без бюджета")  # budget filters work without a subscription
    assert "скрывать" in flt.text
    await tg.press("💰 Бюджет")
    flt = await tg.press("от 30 000")
    assert "от 30 000 ₽" in flt.text
    cats = await tg.press("Категории")
    cats = await tg.press("Выбрать все")
    assert sum(b.text.startswith("✅") and b.callback_data.startswith("c:") for b in cats.buttons) == 23
