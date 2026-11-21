"""Filters: categories, keyword phrases, exclusions, budget, plan limits."""

from radar.config import BASE_DIR, load_product_config
from radar.db import User
from radar.matching import Filters, effective_filters, exclude_candidates, matches, parse_phrases

PRODUCT = load_product_config(BASE_DIR / "config" / "plans.json")


def order(title="Telegram-бот для магазина", desc="Нужен бот на aiogram", cats=(5,), budget=None):
    return {"title": title, "description": desc, "cat_ids": list(cats), "budget": budget, "currency": "₽"}


def test_category_filter():
    assert matches(order(cats=[5]), Filters(categories=[5]))
    assert not matches(order(cats=[3]), Filters(categories=[5]))
    assert matches(order(cats=[3, 5]), Filters(categories=[5]))
    assert matches(order(cats=[3]), Filters())  # empty = all


def test_keywords_match_word_starts_not_substrings():
    assert matches(order(title="Доработать бота"), Filters(keywords=["бот"]))
    assert not matches(order(title="Работа с таблицами", desc="Excel"), Filters(keywords=["бот"]))
    assert matches(order(title="Telegram-бот"), Filters(keywords=["telegram бот"]))  # phrase: all words
    assert not matches(order(title="Telegram канал", desc="посты"), Filters(keywords=["telegram бот"]))
    assert matches(order(title="Парсер Ozon", desc=""), Filters(keywords=["бот", "парсер"]))  # any phrase
    assert matches(order(title="Ёлочные игрушки", desc=""), Filters(keywords=["елоч"]))


def test_excludes_reject():
    f = Filters(keywords=["бот"], excludes=["курсов"])
    assert matches(order(title="Бот для записи"), f)
    assert not matches(order(title="Бот для курсовой работы"), f)


def test_budget_filter_and_orders_without_budget():
    f = Filters(min_budget=10000)
    assert matches(order(budget=15000), f)
    assert not matches(order(budget=5000), f)
    assert matches(order(budget=None), f)  # no budget: shown by default
    assert not matches(order(budget=None), Filters(show_no_budget=False))
    assert matches(order(budget=100), Filters(show_no_budget=False))


def test_parse_phrases():
    assert parse_phrases("Telegram бот, ПАРСЕР;  1С ,, парсер") == ["telegram бот", "парсер", "1с"]
    assert parse_phrases(" , ") == []


def _user(**kw):
    base = dict(id=1, username=None, first_name=None, created_at="", last_seen_at="")
    base.update(kw)
    return User(**base)


def test_filters_are_not_limited_without_subscription():
    import json
    user = _user(categories=json.dumps([5, 2, 3]), keywords=json.dumps(["a", "b", "c", "d"]),
                 excludes=json.dumps(["x"]), min_budget=30000, show_no_budget=0)
    for code in ("free", "pro_week", "pro", "pro_year"):
        f = effective_filters(user, PRODUCT.plan(code))
        assert f.categories == [5, 2, 3] and len(f.keywords) == 4 and f.min_budget == 30000
        assert not f.show_no_budget and f.excludes == ["x"]


def test_plans_free_is_three_searches_total_and_three_subscriptions():
    free = PRODUCT.plan("free")
    assert free.searches == 3 and not free.radar and not free.is_paid
    assert [(p.title, p.price_stars, p.period_days) for p in PRODUCT.for_sale] == [
        ("PRO неделя", 49, 7), ("PRO месяц", 149, 30), ("PRO год", 990, 365)]
    assert all(p.radar and p.searches is None for p in PRODUCT.for_sale)


def test_exclude_candidates_skip_known_and_stop_words():
    words = exclude_candidates("Нужно сделать логотип и фирменный стиль", have=[], keywords=[])
    assert "нужно" not in words and "сделать" not in words
    assert "логот" in words  # stem: «логотип» → «логот» also catches «логотипа»
    assert exclude_candidates("Логотип", have=["логот"], keywords=[]) == []
    assert exclude_candidates("Telegram бот", have=[], keywords=["telegram"]) == []
    assert matches(order(title="Три логотипа"), Filters(excludes=["логот"])) is False
