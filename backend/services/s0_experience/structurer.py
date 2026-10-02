"""
S0 v2 — CV-level experience structure: S0a..S0e orchestration, the S0c AI
structurer call, its single repair call, and the cache identity.

  build_s0(extracted_text, ...) -> S0Document

S0 depends ONLY on the CV text (plus S0/prompt/model versions). It takes no
job, criterion or application parameter and is cached by
sha256(extracted_text) + S0_VERSION + structurer prompt version + model.

The structurer (temperature 0, JSON) receives numbered CV lines and the
deterministic anchor list. It may only reference anchor IDs and line numbers,
classify entry kinds and declare uncertainty; it never writes dates or years.
Its response is accepted only through validator.validate_structure(). On a
violation: ONE repair call carrying the previous response and the exact rule
violations. Still invalid -> structure_status "unverified" (validation_failed,
cached). AI/network failure -> "unverified" (ai_unavailable, retryable, never
cached).

Input/output safety (no CV text is ever truncated — every non-blank line is
sent):
  - request size is checked against a model-context budget using a
    deterministic UTF-8-byte UPPER BOUND on tokens (no tokenizer dependency);
    over budget -> "unverified" (exceeds_model_context, not retryable, cached),
    with no AI call;
  - output allowance is the model maximum (16,384 tokens);
  - finish_reason == "length" on the main or repair call -> "unverified"
    (output_truncated, not retryable, cached); a truncated response is never
    treated as ordinary invalid JSON and never triggers the repair call;
  - finish_reason and token usage of every call are recorded in
    structurer["call_log"].
Chunking and duplicate-block collapsing are documented future options only:
the production CV population (max 19,012 chars, ~4.8k tokens) is far below
the budget. In "unverified" no line is owned: anchors and display-only candidate
blocks are kept; there is no legacy CVFacts fallback.

SHADOW ONLY: no production module imports this package yet.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date
from typing import Any, Protocol

from services.s0_experience import llm_call
from services.s0_experience.dates import extract_anchors
from services.s0_experience.llm_call import request_token_upper_bound
from services.s0_experience.schema import (
    DATES_UNDATED, OWNERSHIP_UNCERTAIN, REASON_AI_UNAVAILABLE, REASON_INTERNAL_ERROR,
    REASON_EXCEEDS_MODEL_CONTEXT, REASON_NO_TEXT, REASON_OUTPUT_TRUNCATED, REASON_VALIDATION_FAILED, RETRYABLE_REASONS, S0_VERSION,
    STRUCTURE_FAILED, STRUCTURE_REPAIRED, STRUCTURE_UNVERIFIED, STRUCTURE_VALIDATED, LineText,
    S0Document, S0Entry, compute_date_status,
)
from services.s0_experience.text import (
    numbered_lines, ocr_noise_indicator, split_lines, text_sha256,
)
from services.s0_experience.validator import ParsedEntry, ValidationResult, validate_structure

logger = logging.getLogger(__name__)

S0C_PROMPT_VERSION = "s0c-1"
S0C_MODEL = "gpt-4o-mini"
S0C_TEMPERATURE = 0.0
# gpt-4o-mini limits and the safe input budget come from the shared helper
# (services.s0_experience.llm_call): 128,000 context, 16,384 output,
# int((128000 - 16384) * 0.9) = 100,454 input.
S0C_MODEL_CONTEXT_TOKENS = llm_call.MODEL_CONTEXT_TOKENS
S0C_MAX_TOKENS = llm_call.MAX_OUTPUT_TOKENS  # output allowance per call (= model maximum)
S0C_INPUT_SAFETY = llm_call.INPUT_SAFETY
S0C_MAX_INPUT_TOKENS = llm_call.MAX_INPUT_TOKENS

S0C_SYSTEM_PROMPT = """You structure the EXPERIENCE HISTORY of one CV. You do not judge relevance to any job.

INPUT
- DATE ANCHORS: every date range found in the CV by software, each with an ID (A1, A2, ...), its line and its text.
- CV LINES: the CV text, one line per row, prefixed with its line number (L0001|). Blank lines are omitted but keep their numbers.

TASK
Identify the candidate's experience entries (jobs, freelance work, internships, volunteering, projects) and also education/training blocks that carry a date anchor. For each entry, point at the lines it consists of.

HARD RULES
1. Never write, compute or correct a date, a duration or a number of years. Refer to dates ONLY by anchor ID.
2. Account for EVERY anchor EXACTLY ONCE: either as the anchor_id of one entry, or in ignored_anchors with a disposition:
   inside_responsibility (a date mentioned inside another entry's responsibilities; give owner_entry = that entry's index in your entries list),
   education, training, certification, non_employment_project, other.
3. Refer to CV lines only by their numbers. title_text / employer_text must be copied VERBATIM from the line you cite (a part of the line is fine).
4. A line belongs to at most ONE entry. Only exception: a single employer line placed directly above several roles at that employer may be the employer_line (and a header line) of each of those roles.
5. Ownership is positive-only. If you are not sure which entry a line belongs to, LEAVE IT OUT of every entry. If you cannot confidently delimit an entry at all, still list it (so its anchor is accounted for) with "ownership": "uncertain".
6. An entry with no date anchor must have "anchor_id": null and an "undated_reason" (e.g. "no dates given", "single year only").
7. kind is one of: employment, freelance, internship, volunteer, project, education, training, other.
8. Do not invent entries. Summary/profile statements ("10 years of experience in ...") are NOT entries.

OUTPUT — JSON only:
{
  "entries": [
    {"anchor_id": "A1" | null,
     "kind": "employment",
     "title_line": 12, "title_text": "Programme Manager",
     "employer_line": 13, "employer_text": "UNDP",
     "header_lines": [12, 13, 14],
     "body_lines": [[15, 19]],
     "ownership": "certain" | "uncertain",
     "undated_reason": null}
  ],
  "ignored_anchors": [
    {"anchor_id": "A4", "disposition": "inside_responsibility", "owner_entry": 0}
  ]
}
header_lines = the entry's title/employer/location/date lines; body_lines = inclusive [first, last] ranges of its responsibility lines. Omit title/employer (null) when the CV does not state them."""


class S0Cache(Protocol):
    def get(self, key: str) -> dict | None: ...
    def set(self, key: str, value: dict) -> None: ...


class InMemoryS0Cache:
    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    def get(self, key: str) -> dict | None:
        return self.store.get(key)

    def set(self, key: str, value: dict) -> None:
        self.store[key] = value


def prompt_fingerprint() -> str:
    return hashlib.sha256(S0C_SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:12]


def s0_cache_key(extracted_text: str, *, prompt_version: str = S0C_PROMPT_VERSION,
                 model: str = S0C_MODEL) -> str:
    """CV-level identity. No job/criterion/application input exists by design."""
    ident = f"{text_sha256(extracted_text)}|{S0_VERSION}|{prompt_version}:{prompt_fingerprint()}|{model}"
    return hashlib.sha256(ident.encode("utf-8")).hexdigest()


def is_cacheable(doc: S0Document) -> bool:
    return not (doc.status_reason in RETRYABLE_REASONS)


_client = None


def _get_client():
    global _client
    if _client is None:
        _client = llm_call.create_client()
    return _client


def build_user_message(lines: list[str], anchors) -> str:
    out = ["DATE ANCHORS:"]
    if anchors:
        for a in anchors:
            span = f"line {a.line}" if a.end_line == a.line else f"lines {a.line}-{a.end_line}"
            out.append(f"{a.anchor_id} | {span} | {a.text!r}")
    else:
        out.append("(none found)")
    out += ["", "CV LINES:", numbered_lines(lines)]
    return "\n".join(out)


def repair_note(errors: list[str]) -> str:
    return ("Your previous JSON violates these rules. Return the COMPLETE corrected JSON "
            "(all entries and ignored_anchors), fixing every violation, changing nothing else, "
            "and still never writing dates:\n- " + "\n- ".join(errors[:40]))


def _candidate_blocks(anchors, line_count: int) -> list[dict]:
    """Display-only windows between consecutive anchors. Never ownership."""
    blocks = []
    lines_sorted = sorted(anchors, key=lambda a: a.line)
    for k, a in enumerate(lines_sorted):
        lo = lines_sorted[k - 1].end_line + 1 if k else 1
        hi = lines_sorted[k + 1].line - 1 if k + 1 < len(lines_sorted) else line_count
        blocks.append({"anchor_id": a.anchor_id, "line_from": max(1, lo),
                       "line_to": max(a.end_line, hi), "display_only": True})
    return blocks


def relocation_flag(rec: dict) -> str:
    return f"line_relocated:{rec['field']}:{rec['from_line']}->{rec['to_line']}:{rec['rule']}"


def _relocation_records(res: ValidationResult, attempt: str) -> list[dict]:
    """Diagnostic form for a response that was NOT assembled (no entry_id yet)."""
    return [{"entry_id": None, "response_entry": pe.index, **r, "attempt": attempt}
            for pe in res.entries for r in pe.relocations]


def _assemble(res: ValidationResult, lines: list[str], anchors, attempt: str = "main"
              ) -> tuple[list[S0Entry], list[dict], list[dict], list[dict]]:
    anchor_by_id = {a.anchor_id: a for a in anchors}
    certain = [pe for pe in res.entries if pe.ownership != OWNERSHIP_UNCERTAIN]
    uncertain = [pe for pe in res.entries if pe.ownership == OWNERSHIP_UNCERTAIN]

    def _first_line(pe: ParsedEntry) -> int:
        a = anchor_by_id.get(pe.anchor_id) if pe.anchor_id else None
        return min([*pe.lines(), *([a.line] if a else [])])

    certain.sort(key=lambda pe: (_first_line(pe), pe.index))
    shared_lines = {}
    for pe in certain:
        for ln in pe.header_lines:
            shared_lines.setdefault(ln, []).append(pe.index)
    index_to_id: dict[int, str] = {}
    entries: list[S0Entry] = []
    relocations: list[dict] = []                         # same records as the entry flags
    for k, pe in enumerate(certain, 1):
        eid = f"E{k}"
        index_to_id[pe.index] = eid
        a = anchor_by_id.get(pe.anchor_id) if pe.anchor_id else None
        valid = bool(a and a.parse_status == "ok")
        interval = a.interval if valid else None          # None for current ranges
        flags = []
        if a and a.parse_status != "ok":
            flags.append(f"anchor_invalid:{a.invalid_reason}")
        if a and a.day_month_ambiguous:
            flags.append("day_month_ambiguous")
        for r in pe.relocations:                         # one record -> flag + validation entry
            rec = {"entry_id": eid, "response_entry": pe.index, **r, "attempt": attempt}
            relocations.append(rec)
            flags.append(relocation_flag(rec))
        owned = sorted(pe.lines())
        entries.append(S0Entry(
            entry_id=eid, anchor_id=pe.anchor_id, kind=pe.kind,
            title=LineText(pe.title_line, pe.title_text) if pe.title_line else None,
            employer=LineText(pe.employer_line, pe.employer_text) if pe.employer_line else None,
            header_lines=tuple(sorted(pe.header_lines)), body_lines=tuple(sorted(pe.body_lines)),
            shared_header_lines=tuple(sorted(ln for ln in pe.header_lines if len(shared_lines[ln]) > 1)),
            undated_reason=pe.undated_reason if pe.anchor_id is None else None,
            interval=interval, duration_months=None if interval is None else interval[1] - interval[0],
            source_text="\n".join(lines[ln - 1] for ln in owned), flags=tuple(flags),
            is_current=bool(valid and a.is_current)))
    ignored = [{"anchor_id": g["anchor_id"], "disposition": g["disposition"],
                "owner_entry_id": index_to_id.get(g["owner_entry"]) if g["owner_entry"] is not None else None}
               for g in res.ignored]
    unc = [{"anchor_id": pe.anchor_id, "kind": pe.kind, "claimed_lines": sorted(pe.lines()),
            "display_only": True} for pe in uncertain]
    return entries, ignored, unc, relocations


async def build_s0(extracted_text: str, *, client: Any = None, model: str = S0C_MODEL,
                   prompt_version: str = S0C_PROMPT_VERSION, today: date | None = None,
                   cache: S0Cache | None = None) -> S0Document:
    key = s0_cache_key(extracted_text, prompt_version=prompt_version, model=model)
    if cache is not None:
        hit = cache.get(key)
        if hit is not None:
            return S0Document.from_dict(hit)
    doc = await _build(extracted_text, key, client, model, prompt_version, today or date.today())
    if cache is not None and is_cacheable(doc):
        cache.set(key, doc.to_dict())
    return doc


async def _build(text: str, key: str, client, model: str, prompt_version: str, today: date) -> S0Document:
    built = (today.year, today.month)          # S0 build month (validity only)
    meta = {"model": model, "prompt_version": prompt_version, "prompt_fingerprint": prompt_fingerprint(),
            "temperature": S0C_TEMPERATURE, "calls": 0, "repair_used": False,
            "max_output_tokens": S0C_MAX_TOKENS, "max_input_tokens": S0C_MAX_INPUT_TOKENS,
            "token_count_method": "utf8_bytes_upper_bound", "call_log": []}
    # built_as_of: parse-time validity only. Durations of present/open ranges are
    # computed later at the scoring as_of (Anchor.interval_at); the cache key
    # contains no date, so time passing never re-calls the structurer.
    base = dict(text_sha256=text_sha256(text), built_as_of=f"{built[0]:04d}-{built[1]:02d}", cache_key=key)
    if not (text or "").strip():
        return S0Document(line_count=0, structure_status=STRUCTURE_FAILED, date_status=DATES_UNDATED,
                          anchors=[], unparsed_date_texts=[], status_reason=REASON_NO_TEXT,
                          structurer=meta, **base)
    try:
        lines = split_lines(text)
        scan = extract_anchors(lines, built)
        ocr = ocr_noise_indicator(lines)
    except Exception:                                   # deterministic stage failure = a bug
        logger.exception("S0 deterministic stage failed")
        return S0Document(line_count=0, structure_status=STRUCTURE_FAILED, date_status=DATES_UNDATED,
                          anchors=[], unparsed_date_texts=[], status_reason=REASON_INTERNAL_ERROR,
                          retryable=True, structurer=meta, **base)

    def _unverified(reason: str, validation: dict) -> S0Document:
        return S0Document(
            line_count=len(lines), structure_status=STRUCTURE_UNVERIFIED,
            date_status=compute_date_status(STRUCTURE_UNVERIFIED, [], scan.anchors, scan.unparsed),
            anchors=scan.anchors, unparsed_date_texts=scan.unparsed,
            candidate_blocks=_candidate_blocks(scan.anchors, len(lines)),
            status_reason=reason, retryable=reason in RETRYABLE_REASONS,
            structurer=meta, ocr=ocr, validation=validation, **base)

    # Full-text guarantee: every non-blank line is sent; nothing is truncated.
    messages = [{"role": "system", "content": S0C_SYSTEM_PROMPT},
                {"role": "user", "content": build_user_message(lines, scan.anchors)}]
    bound = request_token_upper_bound(messages)
    meta["request_token_upper_bound"] = bound
    if bound > S0C_MAX_INPUT_TOKENS:
        return _unverified(REASON_EXCEEDS_MODEL_CONTEXT, {"errors": [], "repair_errors": []})

    client = client or _get_client()
    first_errors: list[str] = []
    try:
        raw, finish = await _call(client, model, messages, meta, "main")
        meta["calls"] = 1
        if finish == "length":
            return _unverified(REASON_OUTPUT_TRUNCATED, {"errors": [], "repair_errors": []})
        res = validate_structure(raw, lines, scan.anchors)
        status = STRUCTURE_VALIDATED
        if not res.ok:
            first_errors = list(res.errors)
            repair_messages = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": repair_note(first_errors)}]
            repair_bound = request_token_upper_bound(repair_messages)
            meta["repair_request_token_upper_bound"] = repair_bound
            if repair_bound > S0C_MAX_INPUT_TOKENS:
                return _unverified(REASON_EXCEEDS_MODEL_CONTEXT,
                                   {"errors": first_errors, "repair_errors": []})
            meta["repair_used"] = True
            raw, finish = await _call(client, model, repair_messages, meta, "repair")
            meta["calls"] = 2
            if finish == "length":
                return _unverified(REASON_OUTPUT_TRUNCATED,
                                   {"errors": first_errors, "repair_errors": []})
            main_res = res
            res = validate_structure(raw, lines, scan.anchors)
            status = STRUCTURE_REPAIRED
            if not res.ok:
                failed = {"errors": first_errors, "repair_errors": list(res.errors)}
                relocs = _relocation_records(main_res, "main") + _relocation_records(res, "repair")
                if relocs:
                    failed["line_relocations"] = relocs
                return _unverified(REASON_VALIDATION_FAILED, failed)
    except Exception as exc:                            # network / API / auth / timeout
        logger.warning("S0 structurer unavailable: %s", exc)
        meta["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return _unverified(REASON_AI_UNAVAILABLE, {"errors": first_errors, "repair_errors": []})

    try:
        entries, ignored, uncertain, relocations = _assemble(
            res, lines, scan.anchors, "repair" if status == STRUCTURE_REPAIRED else "main")
    except Exception:
        logger.exception("S0 assembly failed")
        return S0Document(line_count=len(lines), structure_status=STRUCTURE_FAILED,
                          date_status=DATES_UNDATED, anchors=scan.anchors,
                          unparsed_date_texts=scan.unparsed, status_reason=REASON_INTERNAL_ERROR,
                          retryable=True, structurer=meta, ocr=ocr, **base)
    return S0Document(
        line_count=len(lines), structure_status=status,
        date_status=compute_date_status(status, entries, scan.anchors, scan.unparsed),
        anchors=scan.anchors, unparsed_date_texts=scan.unparsed, entries=entries,
        ignored_anchors=ignored, uncertain_entries=uncertain, structurer=meta, ocr=ocr,
        validation={"errors": first_errors, "repair_errors": [],
                    **({"line_relocations": relocations} if relocations else {})}, **base)


async def _call(client, model: str, messages: list[dict], meta: dict, kind: str) -> tuple[str, str | None]:
    """One chat call. Records finish_reason and token usage (where the API
    returns them) in meta["call_log"]; returns (content, finish_reason)."""
    return await llm_call.chat_json_call(
        client, model=model, messages=messages, temperature=S0C_TEMPERATURE,
        max_tokens=S0C_MAX_TOKENS, call_log=meta["call_log"], kind=kind)


def summarize(doc: S0Document) -> dict:
    """Small, CV-text-free summary for logs/audit."""
    return {"structure_status": doc.structure_status, "date_status": doc.date_status,
            "status_reason": doc.status_reason, "anchors": len(doc.anchors),
            "entries": len(doc.entries), "uncertain_entries": len(doc.uncertain_entries),
            "repair_used": doc.structurer.get("repair_used"), "cache_key": doc.cache_key[:16]}


__all__ = ["build_s0", "s0_cache_key", "is_cacheable", "InMemoryS0Cache", "S0C_SYSTEM_PROMPT",
           "S0C_PROMPT_VERSION", "S0C_MODEL", "build_user_message", "repair_note", "summarize"]
