"""Per-user data: favorites, opened cards, statistics, funnel events; admin overview."""

from __future__ import annotations

import json

from radar.db import Database
from radar.matching import Filters
from radar.utils import ago_iso, msk_day_start_iso, now_iso

FUNNEL = (("start", "/start"), ("filters", "фильтры настроены"), ("search", "искали"), ("pay", "открыли счёт"),
          ("paid", "оплатили"))


class AccountService:
    def __init__(self, db: Database):
        self.db = db

    async def event(self, user_id: int, name: str) -> None:
        await self.db.execute("INSERT OR IGNORE INTO events (user_id, name, created_at) VALUES (?, ?, ?)",
                              user_id, name, now_iso())

    # --- favorites / opens ------------------------------------------------------------------

    async def is_favorite(self, user_id: int, order_id: int) -> bool:
        return bool(await self.db.fetchone("SELECT 1 FROM favorites WHERE user_id = ? AND order_id = ?",
                                           user_id, order_id))

    async def toggle_favorite(self, user_id: int, order_id: int) -> bool:
        """Returns True if the order is now in favorites."""
        if await self.is_favorite(user_id, order_id):
            await self.db.execute("DELETE FROM favorites WHERE user_id = ? AND order_id = ?", user_id, order_id)
            return False
        await self.db.execute("INSERT OR IGNORE INTO favorites (user_id, order_id, created_at) VALUES (?, ?, ?)",
                              user_id, order_id, now_iso())
        return True

    async def favorite_ids(self, user_id: int) -> list[int]:
        rows = await self.db.fetchall("SELECT f.order_id FROM favorites f JOIN orders o ON o.id = f.order_id "
                                      "WHERE f.user_id = ? ORDER BY f.created_at DESC, f.order_id DESC", user_id)
        return [r[0] for r in rows]

    async def opened(self, user_id: int, order_id: int) -> None:
        await self.db.execute("INSERT OR IGNORE INTO opens (user_id, order_id, created_at) VALUES (?, ?, ?)",
                              user_id, order_id, now_iso())

    # --- stats ----------------------------------------------------------------------------------

    async def stats(self, user_id: int, f: Filters, orders_svc) -> dict:
        from radar.matching import matches
        day, week = msk_day_start_iso(), ago_iso(days=7)
        recent = await orders_svc.recent(7)
        found_week = [o for o in recent if matches(o, f)]
        found_day = [o for o in found_week if o["published_at"] >= day]

        async def count(sql, *a):
            return await self.db.scalar(sql, *a) or 0

        return {
            "found_day": len(found_day), "found_week": len(found_week),
            "opened_day": await count("SELECT COUNT(*) FROM opens WHERE user_id = ? AND created_at >= ?", user_id, day),
            "opened_week": await count("SELECT COUNT(*) FROM opens WHERE user_id = ? AND created_at >= ?", user_id, week),
            "radar_day": await count("SELECT COUNT(*) FROM deliveries WHERE user_id = ? AND status = 'sent' "
                                     "AND sent_at >= ?", user_id, day),
            "radar_week": await count("SELECT COUNT(*) FROM deliveries WHERE user_id = ? AND status = 'sent' "
                                      "AND sent_at >= ?", user_id, week),
            "favorites": await count("SELECT COUNT(*) FROM favorites WHERE user_id = ?", user_id),
        }

    # --- admin -------------------------------------------------------------------------------------

    async def overview(self) -> dict:
        day = ago_iso(days=1)
        funnel = []
        for name, label in FUNNEL:
            funnel.append((label, await self.db.scalar("SELECT COUNT(*) FROM events WHERE name = ?", name) or 0))
        return {
            "users": await self.db.scalar("SELECT COUNT(*) FROM users") or 0,
            "users_day": await self.db.scalar("SELECT COUNT(*) FROM users WHERE created_at >= ?", day) or 0,
            "radar_on": await self.db.scalar("SELECT COUNT(*) FROM users WHERE radar_on = 1 AND onboarded = 1 "
                                             "AND blocked = 0") or 0,
            "orders_day": await self.db.scalar("SELECT COUNT(*) FROM orders WHERE fetched_at >= ?", day) or 0,
            "orders": await self.db.scalar("SELECT COUNT(*) FROM orders") or 0,
            "sent_day": await self.db.scalar("SELECT COUNT(*) FROM deliveries WHERE status = 'sent' AND sent_at >= ?",
                                             day) or 0,
            "stars": await self.db.scalar("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'paid' "
                                          "AND currency = 'XTR'") or 0,
            "funnel": funnel,
        }

    async def user_card(self, user_id: int) -> dict | None:
        row = await self.db.fetchone("SELECT * FROM users WHERE id = ?", user_id)
        if not row:
            return None
        d = dict(row)
        for k in ("categories", "keywords", "excludes"):
            d[k] = json.loads(d[k])
        d["searches"] = await self.db.scalar("SELECT COUNT(*) FROM usage_events WHERE user_id = ?", user_id) or 0
        d["favorites"] = await self.db.scalar("SELECT COUNT(*) FROM favorites WHERE user_id = ?", user_id) or 0
        d["sent"] = await self.db.scalar("SELECT COUNT(*) FROM deliveries WHERE user_id = ? AND status = 'sent'",
                                         user_id) or 0
        d["payments"] = [dict(r) for r in await self.db.fetchall(
            "SELECT plan, amount, status, telegram_charge_id, paid_at FROM payments WHERE user_id = ? "
            "AND status != 'pending' ORDER BY id DESC LIMIT 5", user_id)]
        return d

    async def broadcast_targets(self) -> list[int]:
        return [r[0] for r in await self.db.fetchall("SELECT id FROM users WHERE blocked = 0")]
