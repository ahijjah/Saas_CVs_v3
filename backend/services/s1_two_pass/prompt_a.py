"""
Pass A prompt s1a-1.0 (the experience TARGET pass): a pinned, byte/SHA-verified text file.

Derived from the s1-5.2 target/restriction contract with every qualifying-context instruction removed (no
setting field, no context restriction kind, no context ambiguity) and a required target_basis added. The file
ends with the shared security hardening suffix (byte-identical to the one the v3 S1 prompt appends; checked by
a test, never imported here). Loading verifies the SHA-256 on every call; a changed file is an integrity error,
never a silently different prompt.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from services.s1_two_pass.schema import S1A_PROMPT_VERSION

S1A_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / f"{S1A_PROMPT_VERSION}.txt"
S1A_PROMPT_SHA256 = "4caabb71429c0cc1ac997986c2a6c95775b06f4f7acebcb7025e27757f74bf36"


class PromptIntegrityError(RuntimeError):
    """The Pass A prompt file is not the pinned s1a-1.0 text."""


def load_pass_a_prompt() -> str:
    raw = S1A_PROMPT_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != S1A_PROMPT_SHA256:
        raise PromptIntegrityError(f"{S1A_PROMPT_PATH.name} sha256 {digest} != pinned {S1A_PROMPT_SHA256}")
    return raw.decode("utf-8")


def pass_a_prompt_fingerprint() -> str:
    """First 12 hex chars of the prompt SHA-256 (the same fingerprint form as the v3 S1 prompt)."""
    return S1A_PROMPT_SHA256[:12]
