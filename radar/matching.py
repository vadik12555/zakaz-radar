"""Which orders fit a user's filters.

Keywords are comma-separated phrases. A phrase matches when every word of it starts some word of the
order (title + description): «бот» finds «бота», «ботов», but not «работа». Any phrase is enough (OR).
Exclusions use the same rule; one hit rejects the order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from radar.config import Plan

WORD_RE = re.compile(r"[a-zа-я0-9]+")
STOP = {"нужно", "нужен", "нужна", "нужны", "сделать", "создать", "требуется", "ищу", "ищем", "для", "под",
        "заказ", "проект", "работа", "задача", "помощь", "срочно", "который", "которые", "через", "очень",
        "разработка", "доработка", "настройка", "специалист", "исполнитель", "необходимо", "можно", "этот",
        "надо", "либо", "или", "без", "есть", "with", "from", "need", "the", "and", "for"}


def tokens(text: str) -> list[str]:
    return WORD_RE.findall((text or "").lower().replace("ё", "е"))


def parse_phrases(text: str) -> list[str]:
    """'Telegram бот, парсер;  1С ' → ['telegram бот', 'парсер', '1с']"""
    out = []
    for part in re.split(r"[,;\n]+", text or ""):
        phrase = " ".join(tokens(part))[:40]
        if phrase and phrase not in out:
            out.append(phrase)
    return out


def phrase_hits(phrase: str, words: list[str]) -> bool:
    return all(any(w.startswith(p) for w in words) for p in phrase.split())


@dataclass
class Filters:
    categories: list[int] = field(default_factory=list)  # empty = all categories
    keywords: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)
    min_budget: int = 0
    show_no_budget: bool = True


def effective_filters(user, plan: Plan) -> Filters:
    """The user's stored filters cut to what their plan allows (a lapsed PRO keeps its settings stored)."""
    cats = list(user.cats)
    if plan.max_categories is not None:
        cats = cats[: plan.max_categories]
    kws = list(user.kws)
    if plan.max_keywords is not None:
        kws = kws[: plan.max_keywords]
    budget_on = plan.budget_filter
    return Filters(categories=cats, keywords=kws, excludes=list(user.excl),
                   min_budget=user.min_budget if budget_on else 0,
                   show_no_budget=bool(user.show_no_budget) if budget_on else True)


def matches(order, f: Filters) -> bool:
    cat_ids = order["cat_ids"] if isinstance(order, dict) else order.cat_ids
    budget = order["budget"] if isinstance(order, dict) else order.budget
    currency = order["currency"] if isinstance(order, dict) else order.currency
    title = order["title"] if isinstance(order, dict) else order.title
    desc = order["description"] if isinstance(order, dict) else order.description
    if f.categories and not set(f.categories) & set(cat_ids):
        return False
    if budget is None:
        if not f.show_no_budget:
            return False
    elif f.min_budget and (currency or "₽") == "₽" and budget < f.min_budget:
        return False
    words = tokens(f"{title} {desc}")
    if f.excludes and any(phrase_hits(p, words) for p in f.excludes):
        return False
    if f.keywords and not any(phrase_hits(p, words) for p in f.keywords):
        return False
    return True


def stem(word: str) -> str:
    """A crude stem so that one exclusion catches word forms: «иллюстраций» → «иллюстрац»."""
    return word[:-2] if len(word) > 5 else word


def exclude_candidates(title: str, have: list[str], keywords: list[str], limit: int = 4) -> list[str]:
    """Words of a title the user can exclude with «🙈 Скрыть похожие» (deterministic order)."""
    out: list[str] = []
    for w in tokens(title):
        if len(w) < 4 or w in STOP or w.isdigit():
            continue
        s = stem(w)
        if s in out or s in have or any(phrase_hits(k, [w]) for k in keywords):
            continue
        out.append(s)
    out.sort(key=lambda s: -len(s))
    return out[:limit]
