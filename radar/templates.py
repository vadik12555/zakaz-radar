"""Response templates (no AI): the order title and the user's profile are substituted into a short text."""

from __future__ import annotations

from radar.db import User
from radar.utils import fmt_money, short

COPY_LIMIT = 256
VARS = ("заказ", "бюджет", "имя", "специализация", "опыт", "портфолио")


def _values(order: dict, user: User, title_len: int) -> dict[str, str]:
    budget = fmt_money(order["budget"], order.get("currency") or "₽") if order.get("budget") else ""
    return {"заказ": short(order["title"], title_len), "бюджет": budget, "имя": user.p_name or "",
            "специализация": user.p_spec or "", "опыт": user.p_experience or "", "портфолио": user.p_portfolio or ""}


def _default(v: dict[str, str], closing: bool) -> str:
    parts = [f"Здравствуйте! Готов выполнить заказ «{v['заказ']}»."]
    if v["специализация"]:
        parts.append(f"Я — {v['специализация']}" + (f", опыт {v['опыт']}." if v["опыт"] else "."))
    elif v["опыт"]:
        parts.append(f"Опыт: {v['опыт']}.")
    if v["портфолио"]:
        parts.append(f"Портфолио: {v['портфолио']}.")
    if closing:
        parts.append("Готов обсудить детали и сроки.")
    if v["имя"]:
        parts.append(v["имя"])
    return " ".join(parts)


def _custom(template: str, v: dict[str, str]) -> str:
    text = template
    for key in VARS:
        text = text.replace("{" + key + "}", v[key])
    return " ".join(text.split(" ")).strip()


def render_response(order: dict, user: User, custom_allowed: bool) -> str:
    """Shortest variant that still fits the 256-char copy button, if possible."""
    use_custom = bool(custom_allowed and user.template)
    text = ""
    for title_len, closing in ((70, True), (70, False), (45, False), (30, False)):
        v = _values(order, user, title_len)
        text = _custom(user.template, v) if use_custom else _default(v, closing)
        if len(text) <= COPY_LIMIT:
            return text
    return text


def default_template_text() -> str:
    return ("Здравствуйте! Готов выполнить заказ «{заказ}». Я — {специализация}, опыт {опыт}. "
            "Портфолио: {портфолио}. Готов обсудить детали и сроки. {имя}")


def profile_empty(user: User) -> bool:
    return not (user.p_name or user.p_spec or user.p_portfolio or user.p_experience)
