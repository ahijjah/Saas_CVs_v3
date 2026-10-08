"""
Pass A prompt (the experience TARGET pass): pinned, byte/SHA-verified text files, one per version.

  s1a-1.0  the s1-5.2 target/restriction contract with every qualifying-context instruction removed and a required
           target_basis. Kept for audit/replay of its real MAIN run; never edited.
  s1a-1.1  BASELINE CANDIDATE, NOT an approved version (its MAIN hard gate missed by 1/225: one no-target reading,
           which strict F5 blocks downstream; its target-basis run lost 47 sector-restricted readings). Kept runnable
           under its own contract (pass_a.CONTRACTS) so its recorded runs replay exactly. After the s1a-1.0 MAIN
           forensics: phrases saying where / for whom / under what circumstances are never a restriction of any kind
           (never vague, function or role,
           never inside a quoted target); vague narrowed to "relevant / related / similar / in the field";
           "<X> experience", "experience in <X>", "خبرة ... في <X>" name the work X; setting_only only when no role
           or work is named; the "if unsure, a function" rule removed; the closing no-hints OUTPUT example has a
           function target.
  s1a-1.2  WITHDRAWN (audit only; never active, loadable only by explicit version): Option D, s1a-1.1 plus a required
           boolean "names_role_or_work". Its real MAIN run regressed (unsafe loss 1 -> 9, failures 0 -> 5, repairs
           0 -> 66); see scripts/s1_eval_results/pass_a/PASS_A_EVAL_LOG.md. No runtime code reads that field.
  s1a-1.3  CURRENT, a CANDIDATE pending real evaluation (S1-A-1.3 target-basis correction): WHAT is decided before
           WHERE; "<X> experience" / "experience in <X>" name work only when X is work, a place / sector / industry /
           kind of employer or client is a where limit; the only where limit of a statement naming no role and no work
           is quoted verbatim in the new "where_evidence" field with target_basis "setting_only" (audit evidence, never
           a target or setting); vague restored as a restriction; total_experience limits neither work nor where; the
           Arabic "خبرة" + adjective form; one setting_only and one unspecified OUTPUT example.
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
    "s1a-1.2": "f7ec01e2816744322205a889e2834270a30decea625a84355fa47c70e416afd3",
    "s1a-1.3": "0cf68cadc53d05e8e26c75bb94d2ea279f91dcbb65d8663f17b9052f4c98656d",
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


def pass_a_prompt_sha256(version: str | None = None) -> str:
    """The pinned SHA-256 of the current prompt (default) or of a pinned earlier version."""
    return S1A_PROMPT_SHA256 if version is None else PROMPT_SHA256[version]


def pass_a_prompt_fingerprint(version: str | None = None) -> str:
    """First 12 hex chars of the prompt SHA-256 (the same fingerprint form as the v3 S1 prompt)."""
    return pass_a_prompt_sha256(version)[:12]
