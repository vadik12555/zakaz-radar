"""PRO access and Telegram Stars payments (taken from Leadly, one-time purchases only).

Rights are always resolved server-side from the subscriptions table; nothing is activated without a
confirmed `successful_payment`, and the same charge id is never credited twice. Admins get the top plan
forever, without a subscription row.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass

from radar.config import Plan, ProductConfig
from radar.db import Database
from radar.utils import add_days_iso, now_iso

log = logging.getLogger(__name__)

CURRENCY = "XTR"  # Telegram Stars


@dataclass
class Access:
    plan: Plan
    ends_at: str | None = None  # None: free, or forever for admins
    is_admin: bool = False

    @property
    def is_pro(self) -> bool:
        return self.plan.tier > 0


@dataclass
class PaymentResult:
    status: str  # activated | duplicate | unknown_payload | mismatch
    plan: Plan | None = None
    ends_at: str | None = None


class BillingService:
    def __init__(self, db: Database, settings_ref):
        self.db = db
        self._settings = settings_ref  # callable returning current Settings

    @property
    def product(self) -> ProductConfig:
        return self._settings().product

    async def access(self, user_id: int) -> Access:
        if user_id in self._settings().admin_ids:
            return Access(self.product.top, None, is_admin=True)
        now = now_iso()
        rows = await self.db.fetchall(
            "SELECT * FROM subscriptions WHERE user_id = ? AND status = 'active' AND starts_at <= ? AND ends_at > ?",
            user_id, now, now)
        best = Access(self.product.free)
        for r in rows:
            plan = self.product.plan(r["plan"])
            if plan.tier > best.plan.tier or (plan.tier == best.plan.tier and best.ends_at
                                              and r["ends_at"] > best.ends_at):
                best = Access(plan, r["ends_at"])
        if best.is_pro:  # a purchase made during an active period starts after it: show the final date
            last = await self.db.scalar("SELECT MAX(ends_at) FROM subscriptions WHERE user_id = ? "
                                        "AND status = 'active' AND ends_at > ?", user_id, now)
            best.ends_at = max(best.ends_at or "", last or "")
        return best

    async def plan(self, user_id: int) -> Plan:
        return (await self.access(user_id)).plan

    # --- Stars checkout --------------------------------------------------------------

    async def create_invoice(self, user_id: int, plan_code: str) -> tuple[Plan, str]:
        plan = self.product.plans.get(plan_code)
        if not plan or not plan.is_paid or plan.hidden:
            raise ValueError("plan is not purchasable")
        payload = f"buy:{plan.code}:{user_id}:{secrets.token_hex(6)}"
        await self.db.execute(
            "INSERT INTO payments (user_id, plan, amount, currency, status, payload, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?)", user_id, plan.code, plan.price_stars, CURRENCY, payload,
            now_iso())
        return plan, payload

    async def validate_pre_checkout(self, user_id: int, payload: str, currency: str,
                                    amount: int) -> tuple[bool, str | None]:
        row = await self.db.fetchone("SELECT * FROM payments WHERE payload = ?", payload)
        if not row or row["user_id"] != user_id:
            return False, "Счёт не найден. Открой «💎 PRO» и создай новый."
        if row["status"] != "pending":
            return False, "Этот счёт уже оплачен. Создай новый в «💎 PRO»."
        if currency != CURRENCY or amount != row["amount"]:
            return False, "Сумма счёта изменилась. Создай новый счёт в «💎 PRO»."
        if row["plan"] not in self.product.plans:
            return False, "Этот тариф больше недоступен."
        return True, None

    async def confirm_payment(self, user_id: int, payload: str, charge_id: str, provider_charge_id: str | None,
                              currency: str, amount: int) -> PaymentResult:
        """Handle `successful_payment`. Idempotent by Telegram charge id."""
        async with self.db.transaction():
            if await self.db.fetchone("SELECT id FROM payments WHERE telegram_charge_id = ?", charge_id):
                return PaymentResult("duplicate")
            row = await self.db.fetchone("SELECT * FROM payments WHERE payload = ?", payload)
            if not row or row["user_id"] != user_id or row["status"] != "pending":
                log.error("successful_payment with unknown payload for user %s", user_id)
                await self.db.execute(
                    "INSERT INTO payments (user_id, plan, amount, currency, status, payload, telegram_charge_id, "
                    "provider_charge_id, error, created_at, paid_at) VALUES (?, 'unknown', ?, ?, 'paid', ?, ?, ?, "
                    "'unknown payload', ?, ?)", user_id, amount, currency, f"orphan:{charge_id}", charge_id,
                    provider_charge_id, now_iso(), now_iso(), commit=False)
                return PaymentResult("unknown_payload")
            if currency != row["currency"] or amount != row["amount"]:
                await self.db.execute("UPDATE payments SET status = 'paid', telegram_charge_id = ?, "
                                      "provider_charge_id = ?, error = 'amount mismatch', paid_at = ? WHERE id = ?",
                                      charge_id, provider_charge_id, now_iso(), row["id"], commit=False)
                return PaymentResult("mismatch")
            plan = self.product.plan(row["plan"])
            ends_at = await self._extend(user_id, plan, "stars", row["id"], plan.period_days)
            await self.db.execute("UPDATE payments SET status = 'paid', telegram_charge_id = ?, "
                                  "provider_charge_id = ?, paid_at = ? WHERE id = ?",
                                  charge_id, provider_charge_id, now_iso(), row["id"], commit=False)
            return PaymentResult("activated", plan, ends_at)

    async def _extend(self, user_id: int, plan: Plan, source: str, payment_id: int | None, days: int) -> str:
        """A new period starts when the last active PRO period ends (buying twice adds the days up)."""
        now = now_iso()
        last = await self.db.scalar("SELECT MAX(ends_at) FROM subscriptions WHERE user_id = ? AND status = 'active' "
                                    "AND ends_at > ?", user_id, now)
        starts = last or now
        ends = add_days_iso(days, starts)
        await self.db.execute(
            "INSERT INTO subscriptions (user_id, plan, starts_at, ends_at, status, source, payment_id, created_at) "
            "VALUES (?, ?, ?, ?, 'active', ?, ?, ?)", user_id, plan.code, starts, ends, source, payment_id, now,
            commit=False)
        return ends

    # --- admin operations -------------------------------------------------------------

    async def grant(self, user_id: int, days: int, plan_code: str = "pro") -> str:
        plan = self.product.plans.get(plan_code)
        if not plan or not plan.is_paid or days <= 0:
            raise ValueError("unknown paid plan or bad days")
        async with self.db.transaction():
            pid = await self.db.execute(
                "INSERT INTO payments (user_id, plan, amount, currency, status, payload, created_at, paid_at) "
                "VALUES (?, ?, 0, 'manual', 'manual', ?, ?, ?)",
                user_id, plan.code, f"manual:{user_id}:{secrets.token_hex(6)}", now_iso(), now_iso(), commit=False)
            return await self._extend(user_id, plan, "manual", pid, days)

    async def payment_by_charge(self, charge_id: str):
        return await self.db.fetchone("SELECT * FROM payments WHERE telegram_charge_id = ?", charge_id)

    async def mark_refunded(self, charge_id: str) -> None:
        async with self.db.transaction():
            row = await self.payment_by_charge(charge_id)
            if not row:
                return
            await self.db.execute("UPDATE payments SET status = 'refunded' WHERE id = ?", row["id"], commit=False)
            await self.db.execute("UPDATE subscriptions SET status = 'refunded' WHERE payment_id = ?", row["id"],
                                  commit=False)

    async def active_pro_users(self) -> int:
        now = now_iso()
        return await self.db.scalar("SELECT COUNT(DISTINCT user_id) FROM subscriptions WHERE status = 'active' "
                                    "AND starts_at <= ? AND ends_at > ?", now, now) or 0
