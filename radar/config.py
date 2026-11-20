"""Settings: secrets from .env, tariffs and limits from config/plans.json."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
FEED_URL = "https://www.fl.ru/rss/all.xml"


@dataclass(frozen=True)
class Plan:
    code: str
    title: str
    tier: int
    price_stars: int
    period_days: int
    searches: int | None  # free searches for the whole life of the account; None = unlimited
    radar_delay_min: int
    max_categories: int | None
    max_keywords: int | None
    notifications_per_day: int | None
    budget_filter: bool
    custom_templates: bool
    hidden: bool = False

    @property
    def is_paid(self) -> bool:
        return self.price_stars > 0


@dataclass(frozen=True)
class ProductConfig:
    plans: dict[str, Plan]

    def plan(self, code: str | None) -> Plan:
        return self.plans.get(code or "free") or self.plans["free"]

    @property
    def free(self) -> Plan:
        return self.plans["free"]

    @property
    def for_sale(self) -> list[Plan]:
        return sorted((p for p in self.plans.values() if p.is_paid and not p.hidden), key=lambda p: p.price_stars)

    @property
    def top(self) -> Plan:
        """The plan admins get forever: the highest tier, the longest period among equals."""
        return max((p for p in self.plans.values() if not p.hidden), key=lambda p: (p.tier, p.period_days))


def _opt(value) -> int | None:
    return None if value is None else int(value)


def load_product_config(path: Path) -> ProductConfig:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    plans = {}
    for code, p in raw["plans"].items():
        plans[code] = Plan(
            code=code, title=p["title"], tier=int(p.get("tier", 0)), price_stars=int(p.get("price_stars", 0)),
            period_days=int(p.get("period_days", 30)), searches=_opt(p.get("searches")),
            radar_delay_min=int(p.get("radar_delay_min", 0)), max_categories=_opt(p.get("max_categories")),
            max_keywords=_opt(p.get("max_keywords")), notifications_per_day=_opt(p.get("notifications_per_day")),
            budget_filter=bool(p.get("budget_filter", False)), custom_templates=bool(p.get("custom_templates", False)),
            hidden=bool(p.get("hidden", False)),
        )
    if "free" not in plans:
        raise ValueError("plans.json: нужен тариф 'free'")
    return ProductConfig(plans=plans)


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_ids: frozenset[int]
    support_contact: str
    payments_enabled: bool
    db_path: Path
    log_file: Path | None
    log_level: str
    telegram_proxy: str | None
    feed_url: str
    poll_seconds: int
    stale_minutes: int
    keep_days: int
    plans_path: Path
    product: ProductConfig

    def with_product(self, product: ProductConfig) -> "Settings":
        return replace(self, product=product)


def load_settings(env_file: str | Path | None = None, **overrides) -> Settings:
    load_dotenv(env_file or BASE_DIR / ".env")
    plans_path = Path(os.getenv("PLANS_CONFIG", BASE_DIR / "config" / "plans.json"))
    log_file = os.getenv("LOG_FILE", str(BASE_DIR / "data" / "radar.log"))
    values = dict(
        bot_token=os.getenv("BOT_TOKEN", "").strip(),
        admin_ids=frozenset(int(x) for x in _csv(os.getenv("ADMIN_IDS", "")) if x.lstrip("-").isdigit()),
        support_contact=os.getenv("SUPPORT_CONTACT", "").strip(),
        payments_enabled=os.getenv("PAYMENTS_ENABLED", "true").strip().lower() in ("1", "true", "yes"),
        db_path=Path(os.getenv("DB_PATH", BASE_DIR / "data" / "radar.db")),
        log_file=Path(log_file) if log_file else None,
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        telegram_proxy=os.getenv("TELEGRAM_PROXY") or None,
        feed_url=os.getenv("FEED_URL", FEED_URL),
        poll_seconds=max(60, int(os.getenv("POLL_SECONDS", "150"))),
        stale_minutes=int(os.getenv("STALE_MINUTES", "15")),
        keep_days=int(os.getenv("KEEP_DAYS", "14")),
        plans_path=plans_path,
        product=load_product_config(plans_path),
    )
    values.update(overrides)
    return Settings(**values)
