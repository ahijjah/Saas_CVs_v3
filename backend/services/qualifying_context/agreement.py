"""
Deterministic agreement between the stored qualifying context (job level) and ONE criterion's S1 context reading
(P4c resolver, qcr-1). Pure: no model call, no I/O, no DB, no S1 import beyond the JD-text utilities.

  resolve_context(s1_reading, analysis_json, current_jd_text) -> dict   (ContextResolution shape)

``s1_reading`` is plain data produced by the S1 two-pass assembly (its s1_reading helper):
  {"status": "ok" | "unavailable", "settings": [verbatim scope-"all" contexts applied by S1],
   "candidates": [{"text", "scope"}, ...]  (every context S1 found, any scope), "compound": bool}
S1 never imports this module and this module never feeds anything back into an S1 request.

Authority and truth table (first match wins):
  recruiter object (source "recruiter")
      identified                         -> resolved / recruiter (flag jd_changed_since_decision or
                                            jd_version_unknown when the decision hash is not the current JD's)
      none, decision hash == current JD  -> resolved / recruiter
      none, JD edited or hash unknown    -> unconfirmed / stale   (an edit may have added a restriction)
      uncertain                          -> unconfirmed / uncertain
  no valid object
      latest_run failed                  -> unconfirmed / qc_failed
      otherwise (absent or malformed)    -> unconfirmed / unassessed      (never "none")
  analysis object
      hash missing or != current JD      -> unconfirmed / stale
      uncertain                          -> unconfirmed / uncertain
      S1 reading unavailable             -> unconfirmed / s1_unavailable
      identified, canonical sets EQUAL and every S1 candidate has scope "all"
                                         -> resolved / agreed      (effective = the stored texts, jd_verified)
      none and S1 found no candidate at all (any scope)
                                         -> resolved / agreed_none (jd_verified)
      anything else                      -> unconfirmed / disagreement
Only canonical-set equality agrees; a contiguous phrase vs. its split parts is a disagreement (split_of /
merge_of are audit labels only). The comparison is always recorded, including for recruiter decisions.

Canonical form (no fuzzy matching, stemming, synonyms, plural folding or translation): the S1 comparison form
(NFKC, casefold, whitespace collapse), leading/trailing punctuation trimmed, then leading whole tokens from a
closed list (in, within, at, on, the, a, an, في, ضمن, داخل, لدى) stripped repeatedly. Two canonical phrases are
also equal when they differ only by an Arabic proclitic chain on the first word AND both are grounded in the JD.
"""
from __future__ import annotations

import unicodedata
from typing import Any

from services.qualifying_context.runner import jd_sha256
from services.s1_requirements.jd_text import JDText, normalize, proclitic_chain_ok

RESOLVER_VERSION = "qcr-1"
RESOLUTION_SCHEMA = "qc_resolution_v1"

_QC_KEY = "qualifying_context"
_AUDIT_KEY = "qualifying_context_audit"
_STATES = ("identified", "none", "uncertain")
_SOURCES = ("analysis", "recruiter")
_RUN_FAILED = ("failed_technical", "failed_validation")
_RECRUITER_PROVENANCES = ("recruiter_confirmed", "recruiter_edited")

RESOLVED, UNCONFIRMED = "resolved", "unconfirmed"
FLAG_JD_CHANGED = "jd_changed_since_decision"
FLAG_JD_UNKNOWN = "jd_version_unknown"
FLAG_PROVENANCE_UNKNOWN = "recruiter_provenance_unknown"

LEADING_TOKENS = frozenset({"in", "within", "at", "on", "the", "a", "an", "في", "ضمن", "داخل", "لدى"})
# connectors allowed between the parts of a split phrase (audit label split_of / merge_of only, never agreement)
CONNECTOR_TOKENS = LEADING_TOKENS | frozenset({"and", "of", "for", "or", "و", "من", ","})

REL_EQUAL, REL_SUBSET, REL_SUPERSET = "equal", "subset", "superset"
REL_SPLIT_OF, REL_MERGE_OF, REL_OVERLAP, REL_DISJOINT = "split_of", "merge_of", "overlap", "disjoint"
REL_IDENTIFIED_VS_NONE, REL_NONE_VS_IDENTIFIED, REL_NOT_COMPARED = (
    "identified_vs_none", "none_vs_identified", "not_compared")


# ── canonical comparator ─────────────────────────────────────────────────────

def _is_punct(ch: str) -> bool:
    return unicodedata.category(ch).startswith("P")


def canon_tokens(phrase: str) -> tuple[str, ...]:
    s = normalize(phrase or "")
    a, b = 0, len(s)
    while a < b and (_is_punct(s[a]) or s[a] == " "):
        a += 1
    while b > a and (_is_punct(s[b - 1]) or s[b - 1] == " "):
        b -= 1
    toks = s[a:b].split(" ") if b > a else []
    while toks and toks[0] in LEADING_TOKENS:
        toks = toks[1:]
    return tuple(toks)


def canon(phrase: str) -> str:
    return " ".join(canon_tokens(phrase))


def _grounded(jd: JDText, phrase: str) -> bool:
    return bool(phrase.strip()) and bool(jd.find(phrase))


def same_phrase(a: str, b: str, jd: JDText) -> bool:
    ta, tb = canon_tokens(a), canon_tokens(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    if len(ta) != len(tb) or ta[1:] != tb[1:]:
        return False
    long_, short = (ta[0], tb[0]) if len(ta[0]) > len(tb[0]) else (tb[0], ta[0])
    if not (long_.endswith(short) and proclitic_chain_ok(long_[:len(long_) - len(short)], short[0])):
        return False
    return _grounded(jd, a) and _grounded(jd, b)


def _is_split(whole: str, parts: list[str]) -> bool:
    """``whole`` is exactly ``parts`` in order, separated only by closed-list connectors."""
    w = list(canon_tokens(whole))
    i = 0
    for k, p in enumerate(parts):
        pt = list(canon_tokens(p))
        if not pt:
            return False
        if k:
            while i < len(w) and w[i] in CONNECTOR_TOKENS and w[i:i + len(pt)] != pt:
                i += 1
        if w[i:i + len(pt)] != pt:
            return False
        i += len(pt)
    return i == len(w)


def compare(qc: list[str] | None, s1: list[str] | None, jd: JDText) -> str:
    """Relation of the S1 set to the stored set (audit). Only REL_EQUAL ever agrees."""
    if qc is None or s1 is None:
        return REL_NOT_COMPARED
    if not qc and not s1:
        return REL_EQUAL
    if qc and not s1:
        return REL_IDENTIFIED_VS_NONE
    if s1 and not qc:
        return REL_NONE_VS_IDENTIFIED
    s1_in_qc = [any(same_phrase(x, y, jd) for y in qc) for x in s1]
    qc_in_s1 = [any(same_phrase(y, x, jd) for x in s1) for y in qc]
    if all(s1_in_qc) and all(qc_in_s1) and len({canon(x) for x in s1}) == len({canon(y) for y in qc}):
        return REL_EQUAL
    if len(qc) == 1 and len(s1) > 1 and _is_split(qc[0], s1):
        return REL_SPLIT_OF
    if len(s1) == 1 and len(qc) > 1 and _is_split(s1[0], qc):
        return REL_MERGE_OF
    if all(s1_in_qc):
        return REL_SUBSET
    if all(qc_in_s1):
        return REL_SUPERSET
    return REL_OVERLAP if any(s1_in_qc) else REL_DISJOINT


# ── reading the stored object (never coerced) ───────────────────────────────

def _valid_qc(obj: Any) -> dict | None:
    if not isinstance(obj, dict) or set(obj) != {"state", "contexts", "source"}:
        return None
    st, ctx, src = obj["state"], obj["contexts"], obj["source"]
    if st not in _STATES or src not in _SOURCES or not isinstance(ctx, list):
        return None
    if not all(isinstance(c, str) and c.strip() for c in ctx) or len(set(ctx)) != len(ctx):
        return None
    if (st == "identified") != bool(ctx):
        return None
    return {"state": st, "contexts": list(ctx), "source": src}


def _dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _hash(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None


def _valid_reading(r: Any) -> dict | None:
    if not isinstance(r, dict) or r.get("status") not in ("ok", "unavailable"):
        return None
    if r["status"] != "ok":
        return {"status": "unavailable", "settings": None, "candidates": None, "compound": bool(r.get("compound"))}
    st, cands = r.get("settings"), r.get("candidates")
    if not isinstance(st, list) or not all(isinstance(x, str) for x in st):
        return None
    if not isinstance(cands, list) or not all(isinstance(c, dict) and isinstance(c.get("text"), str)
                                              and isinstance(c.get("scope"), str) for c in cands):
        return None
    return {"status": "ok", "settings": list(st), "candidates": [{"text": c["text"], "scope": c["scope"]}
                                                               for c in cands],
            "compound": bool(r.get("compound"))}


def resolve_context(s1_reading: Any, analysis_json: Any, current_jd_text: str) -> dict:
    jd_text = current_jd_text if isinstance(current_jd_text, str) else ""
    jd = JDText(jd_text)
    current_hash = jd_sha256(jd_text)
    analysis = _dict(analysis_json)
    raw_qc = _dict(analysis.get("experience")).get(_QC_KEY)
    audit = _dict(analysis.get(_AUDIT_KEY))
    current, latest = _dict(audit.get("current")), _dict(audit.get("latest_run"))
    qc = _valid_qc(raw_qc)
    reading = _valid_reading(s1_reading)
    if reading is None:
        reading = {"status": "unavailable", "settings": None, "candidates": None, "compound": False}

    # S1 side used for comparison: every candidate text (scope-"all" only agree; others are counted for audit)
    s1_texts = None if reading["candidates"] is None else [c["text"] for c in reading["candidates"]]
    non_all = [] if reading["candidates"] is None else [c for c in reading["candidates"] if c["scope"] != "all"]
    relation = compare(qc["contexts"] if qc and qc["state"] != "uncertain" else None, s1_texts, jd)
    flags: list[str] = []
    decision_hash = _hash(current.get("jd_sha256"))

    def out(status: str, detail: str, effective: dict | None = None) -> dict:
        record = {
            "schema": RESOLUTION_SCHEMA, "resolver_version": RESOLVER_VERSION,
            "analysis": None if qc is None else {**qc, "provenance": current.get("provenance"),
                                                  "jd_sha256": decision_hash},
            "analysis_valid": qc is not None, "analysis_present": raw_qc is not None,
            "latest_run_status": latest.get("status"),
            "s1": reading,
            "comparison": {"relation": relation,
                           "qc_canonical": sorted(canon(c) for c in qc["contexts"]) if qc else None,
                           "s1_canonical": None if s1_texts is None else sorted(canon(c) for c in s1_texts),
                           "s1_non_all_scopes": sorted({c["scope"] for c in non_all})},
            "current_jd_sha256": current_hash, "flags": list(flags),
        }
        return {"status": status, "detail": detail, "effective": effective, "record": record}

    # 1. recruiter decision governs
    if qc is not None and qc["source"] == "recruiter":
        prov = current.get("provenance")
        if prov not in _RECRUITER_PROVENANCES:
            flags.append(FLAG_PROVENANCE_UNKNOWN)
            prov = "recruiter_edited"
        if decision_hash is None:
            flags.append(FLAG_JD_UNKNOWN)
        elif decision_hash != current_hash:
            flags.append(FLAG_JD_CHANGED)
        if qc["state"] == "uncertain":
            return out(UNCONFIRMED, "uncertain")
        if qc["state"] == "none":
            if decision_hash != current_hash:
                return out(UNCONFIRMED, "stale")
            return out(RESOLVED, "recruiter", {"state": "none", "contexts": [], "provenance": prov})
        return out(RESOLVED, "recruiter", {"state": "identified", "contexts": list(qc["contexts"]),
                                           "provenance": prov})

    # 2. no usable object: never "none"
    if qc is None:
        if latest.get("status") in _RUN_FAILED:
            return out(UNCONFIRMED, "qc_failed")
        return out(UNCONFIRMED, "unassessed")

    # 3. analysis object
    if decision_hash is None:
        flags.append(FLAG_JD_UNKNOWN)
        return out(UNCONFIRMED, "stale")
    if decision_hash != current_hash:
        flags.append(FLAG_JD_CHANGED)
        return out(UNCONFIRMED, "stale")
    if qc["state"] == "uncertain":
        return out(UNCONFIRMED, "uncertain")
    if reading["status"] != "ok":
        return out(UNCONFIRMED, "s1_unavailable")
    if qc["state"] == "identified":
        if relation == REL_EQUAL and not non_all:
            return out(RESOLVED, "agreed", {"state": "identified", "contexts": list(qc["contexts"]),
                                            "provenance": "jd_verified"})
        return out(UNCONFIRMED, "disagreement")
    if not reading["candidates"]:
        return out(RESOLVED, "agreed_none", {"state": "none", "contexts": [], "provenance": "jd_verified"})
    return out(UNCONFIRMED, "disagreement")


__all__ = ["resolve_context", "compare", "canon", "same_phrase", "RESOLVER_VERSION", "RESOLUTION_SCHEMA"]
