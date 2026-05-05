from passlib.context import CryptContext

from config import get_settings

settings = get_settings()

# bcrypt cost 10 — matches pgcrypto gen_salt('bf', 10) used in existing DB
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=settings.bcrypt_rounds,
)


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)
