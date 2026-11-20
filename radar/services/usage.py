"""Free search limit stored in the database (Leadly's idempotent charging).

A search is charged with a `ref` that describes it: the same query over the same data (double tap,
pressing the button again) is never charged twice. A search that found nothing is refunded.
"""

from __future__ import annotations

from dataclasses import dataclass

from radar.db import Database
from radar.services.billing import BillingService
from radar.utils import now_iso

SEARCHES = "searches"


@dataclass
class Quota:
    ok: bool
    used: int
    limit: int | None
    already: bool = False  # this exact search was charged before: a free repeat

    @property
    def remaining(self) -> int | None:
        return None if self.limit is None else max(0, self.limit - self.used)


class UsageService:
    def __init__(self, db: Database, billing: BillingService):
        self.db = db
        self.billing = billing

    async def _used(self, user_id: int, kind: str) -> int:
        return await self.db.scalar("SELECT COUNT(*) FROM usage_events WHERE user_id = ? AND kind = ?",
                                    user_id, kind) or 0

    async def peek(self, user_id: int, ref: str | None = None, kind: str = SEARCHES) -> Quota:
        plan = await self.billing.plan(user_id)
        limit = plan.searches
        used = await self._used(user_id, kind)
        if ref and await self.db.fetchone("SELECT 1 FROM usage_events WHERE user_id = ? AND kind = ? AND ref = ?",
                                          user_id, kind, ref):
            return Quota(True, used, limit, already=True)
        return Quota(limit is None or used < limit, used, limit)

    async def charge(self, user_id: int, ref: str, kind: str = SEARCHES) -> Quota:
        """Check and spend atomically. quota.ok=False when the limit is exhausted."""
        async with self.db.transaction():
            quota = await self.peek(user_id, ref, kind)
            if not quota.ok or quota.already or quota.limit is None:
                return quota
            await self.db.execute("INSERT OR IGNORE INTO usage_events (user_id, kind, ref, created_at) "
                                  "VALUES (?, ?, ?, ?)", user_id, kind, ref, now_iso(), commit=False)
            quota.used += 1
            return quota

    async def refund(self, user_id: int, ref: str, kind: str = SEARCHES) -> None:
        await self.db.execute("DELETE FROM usage_events WHERE user_id = ? AND kind = ? AND ref = ?",
                              user_id, kind, ref)

    async def remaining(self, user_id: int) -> int | None:
        return (await self.peek(user_id)).remaining
