-- 011_additional_secrets.sql
-- Seeds additional managed secrets for SMTP, IMAP, Redis, and Celery components.
-- Safe to run multiple times (ON CONFLICT DO NOTHING).

INSERT INTO platform_secrets (key, description, category, is_critical)
VALUES
  ('SMTP_USER',
   'SMTP authentication username or email address for outgoing platform mail',
   'email', FALSE),
  ('IMAP_USER',
   'IMAP authentication username or email address used by the CV inbox polling worker',
   'email', FALSE),
  ('EMAIL_FROM_ADDRESS',
   '"From" address shown on all outbound system emails (e.g. noreply@yourdomain.com)',
   'email', FALSE),
  ('SMTP_HOST',
   'SMTP server hostname or IP address (e.g. smtp.gmail.com, smtp.office365.com)',
   'email', FALSE),
  ('IMAP_HOST',
   'IMAP server hostname or IP address (e.g. imap.gmail.com)',
   'email', FALSE),
  ('REDIS_URL',
   'Full Redis connection URL including auth and DB index (e.g. redis://:pass@host:6379/0)',
   'queue', FALSE),
  ('CELERY_BROKER_URL',
   'Celery message broker connection URL — usually the same as REDIS_URL',
   'queue', FALSE),
  ('CELERY_RESULT_BACKEND',
   'Celery result backend URL for persisting task results (e.g. redis://...)',
   'queue', FALSE)
ON CONFLICT (key) DO NOTHING;
