"""Orders: storage (14 days, dedup by link), search, feed polling and the Radar queue."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Awaitable, Callable

from radar.db import Database, User, order_row
from radar.fl import CATEGORIES, FeedOrder, parse_feed
from radar.matching import Filters, effective_filters, matches, parse_phrases
from radar.services.billing import BillingService
from radar.utils import ago_iso, iso, now_iso, now_utc, parse_iso, today_msk

log = logging.getLogger(__name__)

Fetch = Callable[[str], Awaitable[bytes]]
RADAR_MAX_AGE_HOURS = 3  # older orders found by a poll (e.g. after downtime) are not pushed


@dataclass
class PollResult:
    ok: bool
    fetched: int = 0
    new: int = 0
    queued: int = 0
    error: str | None = None


class OrderService:
    def __init__(self, db: Database, billing: BillingService, settings_ref, fetch: Fetch):
        self.db = db
        self.billing = billing
        self._settings = settings_ref
        self.fetch = fetch
        self.bootstrap_pause = 1.0  # seconds between category feeds on the first start (be polite to FL.ru)

    @property
    def settings(self):
        return self._settings()

    # --- storage ------------------------------------------------------------------------

    async def store(self, orders: list[FeedOrder]) -> list[int]:
        """Insert unseen orders (dedup by link). Returns ids of the new ones."""
        cutoff = ago_iso(days=self.settings.keep_days)
        new_ids = []
        now = now_iso()
        for o in orders:
            if o.published_at < cutoff:
                continue
            oid = await self.db.execute(
                "INSERT OR IGNORE INTO orders (link, fl_id, title, budget, currency, description, categories, cat_ids, "
                "published_at, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", o.link, o.fl_id, o.title, o.budget,
                o.currency, o.description, json.dumps(o.categories, ensure_ascii=False), json.dumps(o.cat_ids),
                o.published_at, now, commit=False)
            if oid and await self.db.scalar("SELECT changes()"):
                new_ids.append(oid)
        await self.db.conn.commit()
        return new_ids

    async def get(self, order_id: int) -> dict | None:
        row = await self.db.fetchone("SELECT * FROM orders WHERE id = ?", order_id)
        return order_row(row) if row else None

    async def many(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        rows = await self.db.fetchall(f"SELECT * FROM orders WHERE id IN ({','.join('?' * len(ids))})", *ids)
        by_id = {r["id"]: order_row(r) for r in rows}
        return [by_id[i] for i in ids if i in by_id]

    async def recent(self, days: int | None = None) -> list[dict]:
        since = ago_iso(days=days or self.settings.keep_days)
        rows = await self.db.fetchall("SELECT * FROM orders WHERE published_at >= ? ORDER BY published_at DESC, id DESC",
                                      since)
        return [order_row(r) for r in rows]

    async def count(self) -> int:
        return await self.db.scalar("SELECT COUNT(*) FROM orders") or 0

    async def cleanup(self) -> int:
        """Delete orders older than keep_days (favorites are kept so the user's list doesn't break)."""
        cutoff = ago_iso(days=self.settings.keep_days)
        n = await self.db.execute("DELETE FROM orders WHERE published_at < ? AND fetched_at < ? "
                                  "AND id NOT IN (SELECT order_id FROM favorites)", cutoff, cutoff)
        await self.db.execute("DELETE FROM deliveries WHERE order_id NOT IN (SELECT id FROM orders)")
        await self.db.execute("DELETE FROM searches WHERE created_at < ?", cutoff)
        return n

    # --- search ---------------------------------------------------------------------------

    async def matching(self, f: Filters, days: int | None = None) -> list[dict]:
        return [o for o in await self.recent(days) if matches(o, f)]

    @staticmethod
    def query_filters(query: str) -> Filters:
        """A one-off query: all its words must be in the order, any category, any budget."""
        phrase = " ".join(parse_phrases(query.replace(",", " ")))
        return Filters(keywords=[phrase] if phrase else [])

    async def search_ref(self, user_id: int, f: Filters, query: str | None) -> str:
        """Idempotency key: the same search over the same data costs one free search."""
        newest = await self.db.scalar("SELECT MAX(id) FROM orders") or 0
        raw = json.dumps([query or "", f.__dict__, newest], ensure_ascii=False, sort_keys=True)
        return "s:" + hashlib.sha1(raw.encode()).hexdigest()[:16]

    async def save_search(self, user_id: int, query: str | None, ids: list[int]) -> int:
        return await self.db.execute("INSERT INTO searches (user_id, query, order_ids, created_at) VALUES (?, ?, ?, ?)",
                                     user_id, query, json.dumps(ids), now_iso())

    async def get_search(self, user_id: int, sid: int) -> dict | None:
        row = await self.db.fetchone("SELECT * FROM searches WHERE id = ? AND user_id = ?", sid, user_id)
        if not row:
            return None
        d = dict(row)
        d["order_ids"] = json.loads(d["order_ids"])
        return d

    # --- polling ----------------------------------------------------------------------------

    async def bootstrap(self) -> int:
        """Empty database (first start): read each category feed once, so search has history at once.
        Returns how many orders were stored. Nothing from the bootstrap is pushed by the Radar."""
        if await self.count():
            return 0
        total = 0
        base = self.settings.feed_url
        for url in [base] + [f"{base}?category={cid}" for cid in CATEGORIES]:
            try:
                total += len(await self.store(parse_feed(await self.fetch(url))))
            except Exception as e:
                log.warning("bootstrap feed %s failed: %s", url, e)
            await asyncio.sleep(self.bootstrap_pause)
        log.info("bootstrap: %s orders", total)
        await self.db.kv_set("last_ok_at", now_iso())
        return total

    async def poll_once(self) -> PollResult:
        try:
            parsed = parse_feed(await self.fetch(self.settings.feed_url))
            if not parsed:
                raise ValueError("feed has no items")
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:300]
            errors = int(await self.db.kv_get("errors_in_row") or 0) + 1
            await self.db.kv_set("errors_in_row", str(errors))
            await self.db.kv_set("last_error", err)
            await self.db.kv_set("last_error_at", now_iso())
            log.warning("feed poll failed (%s in a row): %s", errors, err)
            return PollResult(False, error=err)
        new_ids = await self.store(parsed)
        await self.db.kv_set("errors_in_row", "0")
        await self.db.kv_set("last_ok_at", now_iso())
        queued = await self.queue_radar(new_ids)
        return PollResult(True, len(parsed), len(new_ids), queued)

    async def next_delay(self) -> float:
        """Poll interval; after errors it grows exponentially (up to 30 minutes)."""
        errors = int(await self.db.kv_get("errors_in_row") or 0)
        return min(self.settings.poll_seconds * (2 ** min(errors, 6)), 1800) if errors else self.settings.poll_seconds

    async def status(self) -> dict:
        return {k: await self.db.kv_get(k) for k in ("last_ok_at", "last_error", "last_error_at", "errors_in_row")}

    # --- radar queue --------------------------------------------------------------------------

    async def radar_users(self) -> list[User]:
        rows = await self.db.fetchall("SELECT * FROM users WHERE radar_on = 1 AND onboarded = 1 AND blocked = 0")
        return [User(**dict(r)) for r in rows]

    async def queue_radar(self, order_ids: list[int]) -> int:
        """Match new orders against every Radar user; FREE users get them radar_delay_min later."""
        orders = [o for o in await self.many(order_ids)
                  if o["published_at"] >= ago_iso(hours=RADAR_MAX_AGE_HOURS)]
        if not orders:
            return 0
        queued = 0
        for user in await self.radar_users():
            plan = await self.billing.plan(user.id)
            f = effective_filters(user, plan)
            for o in orders:
                if not matches(o, f):
                    continue
                due = iso(parse_iso(o["fetched_at"]) + timedelta(minutes=plan.radar_delay_min))
                await self.db.execute(
                    "INSERT OR IGNORE INTO deliveries (user_id, order_id, due_at, status, created_at) "
                    "VALUES (?, ?, ?, 'pending', ?)", user.id, o["id"], due, now_iso(), commit=False)
                queued += await self.db.scalar("SELECT changes()") or 0
        await self.db.conn.commit()
        return queued

    async def pending_deliveries(self) -> list[dict]:
        rows = await self.db.fetchall("SELECT * FROM deliveries WHERE status = 'pending' ORDER BY due_at, order_id")
        return [dict(r) for r in rows]

    async def sent_today(self, user_id: int) -> int:
        return await self.db.scalar("SELECT COUNT(*) FROM deliveries WHERE user_id = ? AND status = 'sent' "
                                    "AND sent_day = ?", user_id, today_msk()) or 0

    async def mark_delivery(self, user_id: int, order_id: int, status: str) -> None:
        sent = status == "sent"
        await self.db.execute("UPDATE deliveries SET status = ?, sent_at = ?, sent_day = ? WHERE user_id = ? "
                              "AND order_id = ?", status, now_iso() if sent else None, today_msk() if sent else None,
                              user_id, order_id)

    @staticmethod
    def stale_minutes(last_ok_at: str | None) -> int | None:
        if not last_ok_at:
            return None
        return int((now_utc() - parse_iso(last_ok_at)).total_seconds() // 60)
