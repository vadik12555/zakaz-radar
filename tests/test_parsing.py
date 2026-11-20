"""RSS parsing on a real saved FL.ru feed: fields, budget from the title, categories."""

from radar.fl import CATEGORIES, category_id, parse_budget, parse_feed
from tests.conftest import SAMPLE, make_feed


def test_real_sample_has_all_fields():
    orders = parse_feed(SAMPLE.read_bytes())
    assert len(orders) == 60
    for o in orders:
        assert o.title and o.link.startswith("https://www.fl.ru/projects/") and o.fl_id
        assert o.description and o.published_at.endswith("+00:00")
        assert o.categories and o.cat_ids, o.title  # every section of the sample is a known category
    assert len({o.link for o in orders}) == 60


def test_real_sample_budget_is_taken_from_title():
    orders = parse_feed(SAMPLE.read_bytes())
    with_budget = [o for o in orders if o.budget]
    assert 10 <= len(with_budget) < 60  # not every order has a budget
    for o in with_budget:
        assert "Бюджет" not in o.title and "&#8381;" not in o.title and o.currency == "₽"
    first = next(o for o in orders if o.title.startswith("10 иллюстраций блюд"))
    assert first.budget == 20000 and first.title == "10 иллюстраций блюд до 26 ноября"


def test_description_entities_are_decoded():
    orders = parse_feed(SAMPLE.read_bytes())
    assert not any("&#" in o.description or "<br" in o.description for o in orders)


def test_parse_budget_variants():
    assert parse_budget("Бот (Бюджет: 20 000  &#8381;)") == ("Бот", 20000, "₽")
    assert parse_budget("Бот (Бюджет: 1 500 ₽)") == ("Бот", 1500, "₽")
    assert parse_budget("Сайт (Бюджет: 300 $)") == ("Сайт", 300, "$")
    assert parse_budget("Лендинг для (ремонта)") == ("Лендинг для (ремонта)", None, None)
    assert parse_budget("Просто заказ") == ("Просто заказ", None, None)


def test_category_ids_come_from_the_category_text():
    for cid, (_, section) in CATEGORIES.items():
        assert category_id(f"{section} / Что-то") == cid
    assert category_id("Программирование / Python") == 5
    assert category_id("Неизвестный раздел / X") is None


def test_order_with_two_categories_gets_both_ids():
    data = make_feed([{"id": 1, "title": "Бот", "cats": ["Программирование / Боты", "Мессенджеры / Telegram"]}])
    (o,) = parse_feed(data)
    assert o.cat_ids == [5, 34]


def test_broken_feed_raises():
    import pytest
    with pytest.raises(Exception):
        parse_feed(b"<html>503 Service Unavailable</html>")
