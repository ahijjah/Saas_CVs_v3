# Subscription activation: billing paths

`POST /tenant/subscription/simulate-payment` marks a tenant as paid (`subscription_status = active`) without a payment. It exists for
development and tests only.

- **Production:** refused with 403 before any database statement, whatever `ALLOW_SIMULATED_PAYMENT` says. `ENVIRONMENT=production`
  is the default and the value in `.env.example`.
- **Development and test:** allowed only when `ENVIRONMENT` is `development` or `test` AND `ALLOW_SIMULATED_PAYMENT=true`.
- **Production activation** needs a separate, approved billing path (a payment provider integration with verified webhooks, reviewed
  by billing and security). It does not exist yet. Until it does, production tenants cannot move from `pending_payment` to `active`.
- `POST /tenant/subscription/activate-trial` is a separate, intended free trial (one per email domain). It is not a payment path, and it
  is not changed here.
