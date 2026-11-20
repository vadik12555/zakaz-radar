"""Small helpers: time, Russian plurals, money."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat(timespec="seconds")


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def ago_iso(**delta) -> str:
    return iso(now_utc() - timedelta(**delta))


def add_days_iso(days: int, start: str | None = None) -> str:
    base = datetime.fromisoformat(start) if start else now_utc()
    return iso(base + timedelta(days=days))


def today_msk() -> str:
    return datetime.now(MSK).date().isoformat()


def msk_day_start_iso() -> str:
    start = datetime.now(MSK).replace(hour=0, minute=0, second=0, microsecond=0)
    return iso(start)


def fmt_date(value: str | None) -> str:
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value).astimezone(MSK).strftime("%d.%m.%Y")
    except ValueError:
        return value[:10]


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n) % 100
    if 11 <= n <= 19:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def fmt_money(value: int, currency: str = "₽") -> str:
    return f"{int(value):,}".replace(",", " ") + f" {currency}"


def minutes_between(earlier: str, later: datetime | None = None) -> int:
    later = later or now_utc()
    return max(0, int((later - datetime.fromisoformat(earlier)).total_seconds() // 60))


def fmt_ago(value: str) -> str:
    minutes = minutes_between(value)
    if minutes < 1:
        return "только что"
    if minutes < 60:
        return f"{minutes} {plural(minutes, 'минуту', 'минуты', 'минут')} назад"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} {plural(hours, 'час', 'часа', 'часов')} назад"
    days = hours // 24
    return f"{days} {plural(days, 'день', 'дня', 'дней')} назад"


def short(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def in_quiet_hours(start: int = 23, end: int = 8, at: datetime | None = None) -> bool:
    hour = (at or datetime.now(MSK)).astimezone(MSK).hour
    return hour >= start or hour < end
