from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    # Runtime logins (docs/DATABASE_ROLES.md). The API connects as cv_app. Platform administration uses cv_admin, workers use
    # cv_worker, and secret reads use cv_secrets. An empty value falls back to database_url, for development only.
    # No password is stored here: each URL is supplied by the deployment secret store.
    database_url: str = "postgresql+asyncpg://cv_app@localhost:5432/cv_analyzer"
    admin_database_url: str = ""
    worker_database_url: str = ""
    secrets_database_url: str = ""
    # When true, startup refuses any runtime connection whose role is a superuser or bypasses row-level security.
    enforce_runtime_role_checks: bool = True
    db_schema: str = "cv_analyzer"

    # JWT
    jwt_secret: str = "CHANGE_ME_STRONG_SECRET"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24

    # Password
    password_min_length: int = 9
    bcrypt_rounds: int = 10

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # SMTP
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    email_from_address: str = "noreply@ai970.cloud"
    email_from_name: str = "CV Analyzer"

    # IMAP (for Celery CV intake worker)
    imap_host: str = "mail.ai970.cloud"
    imap_port: int = 993
    imap_user: str = "jobs@ai970.cloud"
    imap_password: str = ""
    imap_use_ssl: bool = True
    imap_poll_interval: int = 120  # seconds

    # File storage
    files_base_path: str = "/files"
    max_file_size_mb: int = 10

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # URLs
    app_base_url: str = "https://app.ai970.cloud"
    api_base_url: str = "https://api.ai970.cloud"

    # CORS
    cors_origins: list[str] = ["https://app.ai970.cloud", "http://localhost:5173"]

    # Intake notifications
    # Default threshold for JOB_NOT_IDENTIFIED recruiter alerts.
    # Overridden at runtime by system_config.unmatched_alert_threshold.
    unmatched_alert_threshold: int = 50

    # Misc
    debug: bool = False
    environment: str = "production"

    # LibreOffice binary path (headless DOCX→PDF)
    libreoffice_bin: str = "soffice"

    # ── Gatekeeper (local pre-filtering) ──────────────────────────────────────
    # Master switch — set to False to always call LLM (useful for testing)
    gatekeeper_enabled: bool = True
    # Semantic similarity floor (0.0-1.0). CVs below this skip the LLM.
    gatekeeper_semantic_threshold: float = 0.40
    # rapidfuzz partial_ratio threshold for skill matching (0-100)
    gatekeeper_skill_fuzzy_threshold: float = 80.0
    # Sentence-transformer model. Production loads it from a local directory prepared at
    # deploy time (docs/RELEASE_RUNBOOK.md, "Sentence-transformer model"). Requests never
    # download it: with embedding_model_local_only=True a missing or unprepared directory
    # fails the API and worker startup, and the readiness endpoint reports it.
    embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    embedding_model_path: str = ""
    embedding_model_local_only: bool = True

    # ── Bilingual output ──────────────────────────────────────────────────────
    # Language for AI-generated human-readable fields in scoring output
    # 'ar' = Arabic (default for Arabic-market SaaS)
    # 'en' = English
    output_language: str = "ar"


@lru_cache
def get_settings() -> Settings:
    return Settings()
