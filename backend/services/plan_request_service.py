"""A tenant may SELECT a plan. Only platform provisioning activates one (see routers/admin.py), until payment integration exists.

Used by POST /tenant/subscription/select-plan and POST /subscription/subscribe, so the two routes cannot diverge again. The trial is never
requested here: it is started only by POST /tenant/subscription/activate-trial, which keeps the once-per-email-domain rule.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.audit_service import log_action
from services.entitlement_service import EXPIRED, VALIDITY_MISSING, load_entitlement

# A tenant may (re)select a plan only from these states, or when it is expired or missing a validity date in time (see below). Suspended and live trial/active tenants cannot change their plan themselves:
# a plan change for them goes through support, so a tenant cannot cut its own access or skip a trial.
REQUESTABLE_FROM = frozenset({"pending_plan_selection", "pending_payment", "pending_sales_contact", "expired", "trial_expired"})


class PlanRequestError(Exception):
    def __init__(self, http_status: int, detail: str):
        super().__init__(detail)
        self.http_status = http_status
        self.detail = detail


def _new_status(plan_code: str) -> str:
    return "pending_sales_contact" if plan_code == "enterprise" else "pending_payment"


async def request_plan(db: AsyncSession, tenant_id: str, plan_code: str, *, user_id: str | None, user_email: str | None) -> dict:
    """Record the requested plan and move the tenant to a pending state. Never sets 'active'. Caller sets the RLS context."""
    if plan_code == "trial":
        raise PlanRequestError(409, "The free trial is started with the trial option and can be used once per email domain.")

    plan = (await db.execute(
        text("SELECT plan_code FROM subscription_plans WHERE plan_code = :code AND status = 'active'"),
        {"code": plan_code},
    )).first()
    if plan is None:
        raise PlanRequestError(404, "Plan not found or inactive")

    current = (await db.execute(
        text("SELECT subscription_status FROM tenants WHERE tenant_id = CAST(:tid AS uuid) FOR UPDATE"),
        {"tid": tenant_id},
    )).mappings().first()
    if current is None:
        raise PlanRequestError(404, "Tenant not found")
    previous = current["subscription_status"]
    # A stored 'active' or 'trial' whose end date has passed is expired in time, so it is requestable as expired. A live one is not.
    by_time = (await load_entitlement(db, tenant_id)).state
    if previous not in REQUESTABLE_FROM and by_time not in (EXPIRED, VALIDITY_MISSING):
        raise PlanRequestError(
            409,
            "Your plan cannot be changed from its current state. Contact support to change your plan.",
        )

    new_status = _new_status(plan_code)
    await db.execute(
        text("""
            UPDATE tenants
            SET pending_plan = :plan_code, subscription_status = :new_status
            WHERE tenant_id = CAST(:tid AS uuid)
        """),
        {"plan_code": plan_code, "new_status": new_status, "tid": tenant_id},
    )
    await log_action(
        db, tenant_id, user_id, user_email, "plan_requested", "tenant", tenant_id,
        {"requested_plan": plan_code, "previous_status": previous, "new_status": new_status},
    )
    await db.commit()

    if new_status == "pending_sales_contact":
        message = "Your enterprise enquiry has been received. Our sales team will contact you shortly."
    else:
        message = "Plan selected. Activation is pending and our team completes it; your access changes once the plan is active."
    return {"success": True, "plan_code": plan_code, "subscription_status": new_status, "message": message}
