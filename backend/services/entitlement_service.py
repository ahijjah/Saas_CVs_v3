"""Server-side entitlement: what a tenant may do RIGHT NOW.

One rule, used by the request path (auth/entitlements.py), public intake (application_intake_service), the scoring worker
(workers/cv_score.py) and the platform provisioning route. Nothing relies on a scheduler: the expiry is compared with the database clock
(now()) at the moment of each check, so an expired tenant is refused on the next request or task.

States
  full              active with subscription_ends_at > now(), or trial with trial_end_at > now(). The only state with writes, AI and intake.
  suspended         subscription_status = 'suspended' (set by platform provisioning).
  expired           'active' or 'trial' whose end date has passed, or an explicit expired/cancelled status.
  not_activated     a plan was selected or requested but activation has not been provisioned (pending_* statuses).
  validity_missing  'active' or 'trial' WITHOUT an end date. Legacy records and records set without dates land here. They are NOT
                    extended: they keep their data and read access but cannot write, run AI or accept intake until provisioning records
                    a validity date. Fail-closed by design.
  blocked           any other status, a NULL status, or a tenant that does not exist. Fail-closed.

Reads are never blocked by this module. Only writes, AI processing and intake use ``full``.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

FULL = "full"
SUSPENDED = "suspended"
EXPIRED = "expired"
NOT_ACTIVATED = "not_activated"
VALIDITY_MISSING = "validity_missing"
BLOCKED = "blocked"

NOT_ACTIVATED_STATUSES = frozenset({"pending_plan_selection", "pending_payment", "pending_sales_contact"})
EXPIRED_STATUSES = frozenset({"expired", "trial_expired", "cancelled", "cancelled_pending_expiry"})

_MESSAGES = {
    FULL: "",
    SUSPENDED: "Your subscription is suspended. Your data stays available read-only. Contact support to restore access.",
    EXPIRED: "Your subscription has expired. Your data stays available read-only. Contact support to restore access.",
    NOT_ACTIVATED: "Your plan is not active yet. Activation is pending and is completed by our team.",
    VALIDITY_MISSING: ("Your subscription has no recorded end date, so recruitment changes are paused. "
                       "Contact support to confirm your plan."),
    BLOCKED: "Recruitment changes are not available for this account. Contact support.",
}


@dataclass(frozen=True)
class Entitlement:
    state: str
    subscription_status: str | None

    @property
    def full(self) -> bool:
        return self.state == FULL

    @property
    def message(self) -> str:
        return _MESSAGES[self.state]

    def as_dict(self) -> dict:
        """The shape returned to the tenant (GET /tenant/usage): the state and whether recruitment changes are allowed."""
        return {"state": self.state, "can_write": self.full, "message": self.message}


def classify(subscription_status: str | None, *, trial_end_missing: bool, trial_live: bool,
             ends_missing: bool, ends_live: bool) -> str:
    """Pure rule, separate from the query so the boundaries can be unit-tested. ``*_live`` means the end date is strictly after now()."""
    if subscription_status == "suspended":
        return SUSPENDED
    if subscription_status == "active":
        if ends_missing:
            return VALIDITY_MISSING
        return FULL if ends_live else EXPIRED
    if subscription_status == "trial":
        if trial_end_missing:
            return VALIDITY_MISSING
        return FULL if trial_live else EXPIRED
    if subscription_status in NOT_ACTIVATED_STATUSES:
        return NOT_ACTIVATED
    if subscription_status in EXPIRED_STATUSES:
        return EXPIRED
    return BLOCKED


async def load_entitlement(db: AsyncSession, tenant_id: str) -> Entitlement:
    """Read the tenant's status and dates and classify them against the database clock. The caller sets the RLS context first."""
    row = (await db.execute(
        text("""
            SELECT subscription_status,
                   trial_end_at IS NULL                AS trial_end_missing,
                   COALESCE(trial_end_at > now(), false)  AS trial_live,
                   subscription_ends_at IS NULL        AS ends_missing,
                   COALESCE(subscription_ends_at > now(), false) AS ends_live
            FROM tenants WHERE tenant_id = CAST(:tid AS uuid)
        """),
        {"tid": tenant_id},
    )).mappings().first()
    if row is None:
        return Entitlement(state=BLOCKED, subscription_status=None)
    state = classify(
        row["subscription_status"],
        trial_end_missing=row["trial_end_missing"],
        trial_live=row["trial_live"],
        ends_missing=row["ends_missing"],
        ends_live=row["ends_live"],
    )
    return Entitlement(state=state, subscription_status=row["subscription_status"])
