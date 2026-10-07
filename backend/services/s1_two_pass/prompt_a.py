"""
Pass A prompt (the experience TARGET pass): pinned, byte/SHA-verified text files, one per version.

  s1a-1.0  the s1-5.2 target/restriction contract with every qualifying-context instruction removed and a required
           target_basis. Kept for audit/replay of its real MAIN run; never edited.
  s1a-1.1  (current) after the s1a-1.0 MAIN forensics: phrases saying where / for whom / under what circumstances
           are never a restriction of any kind (never vague, function or role, never inside a quoted target);
           vague narrowed to "relevant / related / similar / in the field"; "<X> experience", "experience in
           <X>", "خبرة ... في <X>" name the work X; setting_only only when no role or work is named; the "if unsure,
           a function" rule removed; the closing no-hints OUTPUT example has a function target.
The files end with the shared security hardening suffix (byte-identical to the one the v3 S1 prompt appends;
checked by a test, never imported here). Loading verifies the SHA-256 on every call; a changed file is an
integrity error, never a silently different prompt.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from services.s1_two_pass.schema import S1A_PROMPT_VERSION

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
PROMPT_SHA256 = {
    "s1a-1.0": "4caabb71429c0cc1ac997986c2a6c95775b06f4f7acebcb7025e27757f74bf36",
    "s1a-1.1": "952299303431f68d62b9544d6897baa488855c37c22d0fd2890789b15d463e11",
}
S1A_PROMPT_PATH = PROMPT_DIR / f"{S1A_PROMPT_VERSION}.txt"
S1A_PROMPT_SHA256 = PROMPT_SHA256[S1A_PROMPT_VERSION]


class PromptIntegrityError(RuntimeError):
    """A Pass A prompt file is not its pinned text."""


def load_pass_a_prompt(version: str | None = None) -> str:
    """The current prompt (default) or a pinned earlier version (audit / replay)."""
    if version is None:
        path, pinned = S1A_PROMPT_PATH, S1A_PROMPT_SHA256
    else:
        path, pinned = PROMPT_DIR / f"{version}.txt", PROMPT_SHA256[version]
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != pinned:
        raise PromptIntegrityError(f"{path.name} sha256 {digest} != pinned {pinned}")
    return raw.decode("utf-8")


def pass_a_prompt_fingerprint() -> str:
    """First 12 hex chars of the prompt SHA-256 (the same fingerprint form as the v3 S1 prompt)."""
    return S1A_PROMPT_SHA256[:12]
