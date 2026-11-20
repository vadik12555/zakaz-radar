"""Service container shared by handlers and background jobs."""

from __future__ import annotations

from dataclasses import dataclass

from radar.config import Settings
from radar.db import Database
from radar.fl import FeedClient
from radar.services.account import AccountService
from radar.services.billing import BillingService
from radar.services.orders import Fetch, OrderService
from radar.services.usage import UsageService


@dataclass
class Services:
    settings: Settings
    db: Database
    billing: BillingService
    usage: UsageService
    orders: OrderService
    account: AccountService


def build_services(settings: Settings, db: Database, fetch: Fetch | None = None) -> Services:
    holder: dict = {"settings": settings}

    def ref() -> Settings:
        return holder["settings"]

    billing = BillingService(db, ref)
    svc = Services(settings=settings, db=db, billing=billing, usage=UsageService(db, billing),
                   orders=OrderService(db, billing, ref, fetch or FeedClient().fetch), account=AccountService(db))
    return svc
