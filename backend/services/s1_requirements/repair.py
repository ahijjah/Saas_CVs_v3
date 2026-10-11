"""
S1 scoped repair merge (s1-4).

The single repair call may return a complete answer, but only the fields and
targets that FAILED validation in the main answer are taken from it; every other
primitive is kept from the main answer. The merged answer is then validated
again (by the caller); if it is still invalid the criterion is failed_validation.
A repair can therefore never replace valid targets, types, matches, mappings,
spans or a relevance basis just because its rewrite happens to be
self-consistent.

Merge rules per criterion (scopes come from validator.ScopedError):
  no error in the main answer      -> main item kept untouched
  "criterion" (missing/duplicate)  -> repair item taken
  requirement_spans / duration / ambiguity / relevance_basis
                                   -> that field taken from the repair
  settings / setting:Sk (s1-6)     -> the repair's settings list ONLY if no context
                                      of the main answer disappears (see
                                      contexts_preserved); otherwise the main list
                                      is kept (and the merge stays invalid)
  target:Tn (hint target, whole)   -> that hint's target object taken from the
                                      repair (missing/duplicate hint)
  target:Tn:<field>                -> ONLY that field of that hint target taken
                                      from the repair (e.g. a bad jd_span copy
                                      may fix the span but can never change the
                                      type or match); every other hint target and
                                      every other field is kept
  targets_extra                    -> hint targets kept from the main answer; only
                                      the repair's non-hint targets are taken (if
                                      it still adds any, the merge stays invalid)
  targets_extra (no hints)         -> criteria without analysis targets must not
                                      return targets (they are derived from
                                      restrictions): only the repair's list is
                                      taken; if it still has targets, invalid
  restriction:Rk / restrictions    -> (criteria without analysis targets) the
                                      repair's restriction list ONLY if every
                                      error-free main restriction is kept
                                      unchanged and every restriction kind of the
                                      main answer is still present: a repair can
                                      fix a restriction but never broaden the
                                      requirement (e.g. into total experience);
                                      s1-6: in addition no context restriction of
                                      the main answer may disappear
                                      (contexts_preserved) and a main entry of
                                      unknown kind must reappear with its text;
                                      otherwise the main list is kept
  "response" (unparseable / malformed main answer, unknown criterion id)
                                   -> the repair answer is taken as a whole

s1-5.2.1 pair-level merge: when EVERY error touching a hint target is a
structured pair-local issue (validator Issue unit "pair": a relation, one-word
or not-found error of an alignment pair whose hint words are target words),
only those main pairs may be replaced, by the repair pairs that cover exactly
the same target words (matched by words, never by position; ambiguous,
overlapping or partial replacements are refused and the main pair stays).
Every other main pair, match, jd_span, jd_extra and type are kept. Coverage,
span, match and other errors keep the whole-field merge above.
s1-5.2.1 material lock: a jd_extra word the MAIN answer marked "material" can
never be relabelled, removed, paired or hidden by a new jd_span in the merged
target (any merge path); the only accepted change is giving up (match none).
Otherwise the main target is kept. The merged answer is always re-validated.

s1-6 context preservation (contexts_preserved): dropping a context broadens the requirement, so a repair list
is taken only when (a) every error-free main context is still present (same line and normalised text) or lies
inside a repair context on the same line (a merge into one contiguous phrase), and (b) the repair has at least
as many contexts as the main answer minus the main contexts (valid or not) absorbed that way. A main context that
was itself invalid (not verbatim, outside the requirement spans, overlapping) therefore still has to be
replaced by a context, never just removed; a repair that cannot keep them all is not taken (fail closed).
The only exception: when the merged answer reports ambiguous_context_scope, its contexts may be removed (that
code keeps the criterion needs_confirmation with no S2 view, so removing them cannot broaden anything).

P4b RC1 (hint-less criteria, _merge_hintless): a hint-less criterion has no "settings" by contract, so a main
answer that put its contexts there is not authoritative in that FIELD, but its contexts still count: the merged
answer never keeps the main "settings"; each main setting must reappear as a "context" restriction of the
repair on the same line (same_context: the same phrase, a longer phrase containing it, or the phrase without a
leading in / on / within / at / the / a / an / في / ضمن / لدى / داخل), otherwise the repair is not taken
(fail closed). And a restriction list that ONLY the repair supplies (the main answer had no usable list) must
name a role or function: a repair is never the sole source of a context-only, vague or total-experience
(pure-duration) reading, so it can never silently turn a role/function criterion into one.

s1-5.1 deterministic withdrawal (plan_withdrawal / apply_withdrawal), AFTER the
one repair, only when the merged answer is still invalid: if every remaining
error of a criterion belongs to ONE hint target's equivalent claim (scopes
target:Tn:match / jd_span / alignment / jd_extra only) and that target's match
is "equivalent", the claim is withdrawn: match none, jd_span null, alignment
[], jd_extra []. Nothing else is touched (type, spans, restrictions, duration,
setting, ambiguity). Any other remaining error anywhere -> no withdrawal at all
(failed_validation). The withdrawn answer is validated again from scratch. This
can only narrow evidence: a withdrawn target is never exact, equivalent or
jd_asserted.
"""
from __future__ import annotations

import copy
import json
from collections import Counter, defaultdict

from services.s1_requirements.criteria import CriterionInput
from services.s1_requirements.jd_text import exact_definition, normalize
from services.s1_requirements.schema import (
    AMB_AMBIGUOUS_CONTEXT_SCOPE, RESTRICTION_CONTEXT, RESTRICTION_FUNCTION, RESTRICTION_KINDS, RESTRICTION_ROLE,
)
from services.s1_requirements.validator import (
    UNIT_PAIR, tokens,
    SCOPE_AMBIGUITY, SCOPE_BASIS, SCOPE_CRITERION, SCOPE_DURATION, SCOPE_RESPONSE, SCOPE_SETTING_PREFIX,
    SCOPE_SETTINGS, SCOPE_SPANS,
    SCOPE_RESTRICTION_PREFIX, SCOPE_RESTRICTIONS, SCOPE_TARGET_PREFIX, SCOPE_TARGETS_EXTRA,
    ScopedError, hint_ids,
)

FIELD_SCOPES = (SCOPE_SPANS, SCOPE_DURATION, SCOPE_AMBIGUITY, SCOPE_BASIS)


def _items(raw: str) -> dict[str, dict] | None:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    items = data.get("criteria") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    out: dict[str, dict] = {}
    for it in items:
        if isinstance(it, dict) and isinstance(it.get("criterion_id"), str) and it["criterion_id"] not in out:
            out[it["criterion_id"]] = it
    return out


WITHDRAWABLE_FIELDS = ("match", "jd_span", "alignment", "jd_extra")


def plan_withdrawal(scoped: list[ScopedError], raw: str, criteria: list[CriterionInput]
                    ) -> dict[str, tuple[str, list[str]]] | None:
    """-> {criterion_id: (hint id, error messages)} if EVERY remaining error is withdrawable, else None."""
    items = _items(raw)
    if items is None or not scoped:
        return None
    hints_by_cid = {c.criterion_id: hint_ids(c) for c in criteria}
    plan: dict[str, tuple[str, list[str]]] = {}
    for e in scoped:
        if e.criterion_id not in hints_by_cid or not e.scopes:
            return None
        hids = set()
        for sc in e.scopes:
            parts = sc.split(":")
            if not (len(parts) == 3 and sc.startswith(SCOPE_TARGET_PREFIX) and parts[2] in WITHDRAWABLE_FIELDS):
                return None                                    # any non-claim error blocks withdrawal
            hids.add(parts[1])
        if len(hids) != 1:
            return None                                        # an error spanning two targets
        (hid,) = hids
        prev = plan.get(e.criterion_id)
        if hid not in hints_by_cid[e.criterion_id] or (prev and prev[0] != hid):
            return None                                        # more than ONE target's claim in a criterion
        plan.setdefault(e.criterion_id, (hid, []))[1].append(e.message)
    for cid, (hid, _) in plan.items():
        item = items.get(cid)
        ts = item.get("targets") if isinstance(item, dict) else None
        objs = [t for t in ts if isinstance(t, dict) and t.get("hint") == hid] if isinstance(ts, list) else []
        if len(objs) != 1 or objs[0].get("match") != "equivalent":
            return None
    return plan


def apply_withdrawal(raw: str, plan: dict[str, tuple[str, list[str]]]) -> tuple[str, dict[str, dict]]:
    """Withdraw each planned equivalent claim -> (new raw answer, {criterion_id: audit record})."""
    data = json.loads(raw)
    audit: dict[str, dict] = {}
    for it in data["criteria"]:
        cid = it.get("criterion_id") if isinstance(it, dict) else None
        if cid not in plan or cid in audit:
            continue
        hid, errors = plan[cid]
        t = _hint_target(it, hid)
        audit[cid] = {"hint": hid, "type": t.get("type"),
                      "withdrawn_claim": {k: copy.deepcopy(t.get(k)) for k in WITHDRAWABLE_FIELDS},
                      "errors": list(errors)}
        t.update({"match": "none", "jd_span": None, "alignment": [], "jd_extra": []})
    return json.dumps(data, ensure_ascii=False), audit


def _pair_local_issues(scoped: list[ScopedError], cid: str, hid: str) -> dict[int, tuple[str, ...]] | None:
    """{main alignment index: target words} when EVERY error touching this hint target is a structured,
    provably pair-local issue (validator Issue unit "pair" with the pair's target words); else None."""
    own = [e for e in scoped if e.criterion_id == cid and any(
        sc == f"{SCOPE_TARGET_PREFIX}{hid}" or sc.startswith(f"{SCOPE_TARGET_PREFIX}{hid}:") for sc in e.scopes)]
    if not own:
        return None
    out: dict[int, tuple[str, ...]] = {}
    for e in own:
        i = e.issue
        if i is None or i.unit != UNIT_PAIR or i.target != hid or i.index is None or not i.words:
            return None
        if out.get(i.index, i.words) != i.words:
            return None
        out[i.index] = i.words
    return out


def normalize_duplicate_representations(scoped: list[ScopedError], raw: str, criteria: list[CriterionInput]
                                        ) -> tuple[str, list[dict]]:
    """s1-5.2.2, BEFORE any repair call: a jd_span that is exactly the JD's own abbreviation definition
    ("Project Manager (P.M.)") is narrowed to the one representation the model ALREADY aligned ("P.M."),
    when that is the ONLY error of the target, the target has exactly one abbreviation pair covering all its
    words, no jd_extra, and the span is exactly one recognised definition (so no qualifier lies outside it).
    No new claim: the alignment is unchanged and the definition stays in the requirement span. -> (raw, records)"""
    items = _items(raw)
    if items is None:
        return raw, []
    hints_by_cid = {c.criterion_id: hint_ids(c) for c in criteria}
    plan: dict[tuple[str, str], dict] = {}
    for e in scoped:
        i = e.issue
        if not (i is not None and i.kind == "duplicate_representation" and e.criterion_id in hints_by_cid
                and i.target in hints_by_cid[e.criterion_id]):
            continue
        cid, hid = e.criterion_id, i.target
        own = [x for x in scoped if x.criterion_id == cid and any(
            sc == f"{SCOPE_TARGET_PREFIX}{hid}" or sc.startswith(f"{SCOPE_TARGET_PREFIX}{hid}:") for sc in x.scopes)]
        objs = _hint_targets(items.get(cid) or {}, hid)
        if own != [e] or len(objs) != 1:
            continue
        t = objs[0]
        sp, al = t.get("jd_span"), t.get("alignment")
        if not (t.get("match") == "equivalent" and isinstance(sp, dict) and isinstance(sp.get("text"), str)
                and isinstance(sp.get("line"), int) and not t.get("jd_extra") and isinstance(al, list)
                and len(al) == 1 and isinstance(al[0], dict) and al[0].get("relation") == "abbreviation"
                and isinstance(al[0].get("hint"), str) and isinstance(al[0].get("jd"), str)
                and tokens(al[0]["hint"]) == tokens(hints_by_cid[cid][hid])):
            continue
        d = exact_definition(sp["text"])
        if d is None:
            continue
        side = tokens(al[0]["jd"])
        new = d.acronym_text if side == [d.acronym] else d.full_text if side == list(d.full_words) else None
        if new is None:
            continue
        plan[(cid, hid)] = {"criterion_id": cid, "hint": hid, "original_span": sp["text"], "normalized_span": new,
                            "line": sp["line"], "reason": "duplicate_representation", "definition": d.to_dict()}
    if not plan:
        return raw, []
    data = json.loads(raw)
    for it in data["criteria"]:
        cid = it.get("criterion_id") if isinstance(it, dict) else None
        for hid in hints_by_cid.get(cid, {}):
            rec = plan.get((cid, hid))
            if rec:
                _hint_target(it, hid)["jd_span"] = {"line": rec["line"], "text": rec["normalized_span"]}
    return json.dumps(data, ensure_ascii=False), list(plan.values())


def pair_repair_guidance(scoped: list[ScopedError]) -> list[str]:
    """Repair-call lines naming the exact alignment pairs that will be taken (pair-local targets only)."""
    lines, seen = [], set()
    for e in scoped:
        i = e.issue
        if not (e.criterion_id and i is not None and i.unit == UNIT_PAIR and i.target):
            continue
        key = (e.criterion_id, i.target)
        if key in seen:
            continue
        seen.add(key)
        local = _pair_local_issues(scoped, *key)
        if local:
            pairs = ", ".join(f"alignment[{k}] (target word(s) {' '.join(w)!r})" for k, w in sorted(local.items()))
            lines.append(f"criterion {key[0]} target {key[1]}: ONLY {pairs} will be taken from your answer; give "
                         f"the corrected pair(s) for exactly those target words. Every other pair, match, jd_span "
                         f"and jd_extra of this target is kept from your previous answer.")
    return lines


def _merge_pairs(main_al: list, rt: dict | None, invalid: dict[int, tuple[str, ...]]) -> tuple[list, list[int]]:
    """Main alignment with ONLY the invalid pairs replaced. A replacement is the set of repair pairs whose
    target words lie inside the invalid pair's words; it is taken only if those pairs cover the invalid pair's
    words exactly once (no overlap, no duplicate, no ambiguity). Main order is kept; every other main pair is
    kept exactly. The caller always re-validates the complete result."""
    rep_al = rt.get("alignment") if isinstance(rt, dict) else None
    rep_pairs = [(p, Counter(tokens(p["hint"]))) for p in rep_al if isinstance(p, dict)
                 and isinstance(p.get("hint"), str)] if isinstance(rep_al, list) else []
    out, replaced = [], []
    for k, p in enumerate(main_al):
        if k not in invalid:
            out.append(copy.deepcopy(p))
            continue
        need = Counter(invalid[k])
        cand = [(q, c) for q, c in rep_pairs if c and not (c - need)]
        if cand and sum((c for _, c in cand), Counter()) == need:
            out.extend(copy.deepcopy(q) for q, _ in cand)
            replaced.append(k)
        else:
            out.append(copy.deepcopy(p))                       # no unambiguous replacement: main pair stays
    return out, replaced


def _material_words(t: dict | None) -> set[str]:
    ex = t.get("jd_extra") if isinstance(t, dict) and t.get("match") == "equivalent" else None
    return {w for e in ex if isinstance(e, dict) and e.get("kind") == "material" and isinstance(e.get("text"), str)
            for w in tokens(e["text"])} if isinstance(ex, list) else set()


def _material_lock_holds(main_t: dict, merged_t: dict) -> bool:
    """s1-5.2.1: a word the MAIN answer marked material stays a material jd_extra item of the same jd_span, or
    the claim is given up (match none). It can never be relabelled, removed, paired or hidden by a new span."""
    locked = _material_words(main_t)
    if not locked:
        return True
    if merged_t.get("match") == "none":
        return True
    paired = {w for p in merged_t.get("alignment") or [] if isinstance(p, dict) and isinstance(p.get("jd"), str)
              for w in tokens(p["jd"])}
    return (merged_t.get("match") == "equivalent" and merged_t.get("jd_span") == main_t.get("jd_span")
            and locked <= _material_words(merged_t) and not (locked & paired))


def _hint_targets(item: dict, hid: str) -> list[dict]:
    targets = item.get("targets")
    return [t for t in targets if isinstance(t, dict) and t.get("hint") == hid] if isinstance(targets, list) else []


def _hint_target(item: dict, hid: str) -> dict | None:
    targets = item.get("targets")
    for t in targets if isinstance(targets, list) else []:
        if isinstance(t, dict) and t.get("hint") == hid:
            return t
    return None


def _ctx_key(x) -> tuple | None:
    if not isinstance(x, dict) or not isinstance(x.get("text"), str):
        return None
    return (x.get("line"), normalize(x["text"]))


def _inside_text(small: tuple, big: tuple) -> bool:
    """same line and the small context's words are a contiguous run of the big one's (word-bounded)."""
    if small is None or big is None or small[0] != big[0]:
        return False
    a, b = tokens(small[1]), tokens(big[1])
    n = len(a)
    return 0 < n <= len(b) and any(b[i:i + n] == a for i in range(len(b) - n + 1))


def contexts_preserved(main: list, rep: list, bad: set[int]) -> bool:
    """s1-6: may the repair's context list replace the main one without dropping a context? ``main`` and ``rep``
    are raw context objects ({"line", "text", ...}); ``bad`` are indices of main entries that failed validation."""
    rep_keys = [_ctx_key(x) for x in rep]
    absorbed = 0
    for j, x in enumerate(main):
        k = _ctx_key(x)
        if k is not None and k in rep_keys:
            continue                                     # kept as it was
        if k is not None and any(_inside_text(k, rk) for rk in rep_keys if rk is not None):
            absorbed += 1                                # merged into one contiguous repair context
            continue
        if j not in bad:
            return False                                 # an error-free context disappeared
    return len(rep) >= len(main) - absorbed              # every other (invalid) context was replaced


LEADING_FUNCTION_WORDS = frozenset({"in", "on", "within", "at", "the", "a", "an", "في", "ضمن", "لدى", "داخل"})


def same_context(main_item, rep_item) -> bool:
    """P4b RC1: does a repair context restriction carry the same context as a main (misplaced) setting? Same
    line (when the main gives one) and either the repair phrase contains the main phrase as a contiguous word
    run, or it is the main phrase minus leading function words only (closed list). Nothing else."""
    if isinstance(main_item, str):
        main_item = {"line": None, "text": main_item}
    if not (isinstance(main_item, dict) and isinstance(rep_item, dict) and isinstance(main_item.get("text"), str)
            and isinstance(rep_item.get("text"), str)):
        return False
    if main_item.get("line") is not None and main_item.get("line") != rep_item.get("line"):
        return False
    a, b = tokens(main_item["text"]), tokens(rep_item["text"])
    if not a or not b:
        return False
    if any(b[i:i + len(a)] == a for i in range(len(b) - len(a) + 1)):
        return True                                      # same phrase, or a longer one containing it
    k = len(a) - len(b)
    return k > 0 and a[k:] == b and all(w in LEADING_FUNCTION_WORDS for w in a[:k])


def _names_target(rr: list) -> bool:
    return any(isinstance(x, dict) and x.get("kind") in (RESTRICTION_ROLE, RESTRICTION_FUNCTION) for x in rr)


def _merge_hintless(m: dict, r: dict, sc: set[str], scope_ambiguous: bool) -> tuple[list | None, bool]:
    """P4b RC1: restrictions of a hint-less criterion whose main answer may also carry (invalid) settings.
    -> (restrictions to use, taken_from_repair). When taken, the caller drops "settings"."""
    sc = set(sc)
    if isinstance(m.get("settings"), list) and m["settings"] and not isinstance(m.get("restrictions"), list):
        sc.add(SCOPE_RESTRICTIONS)
    rr, took = _merge_restrictions(m, r, sc, scope_ambiguous)
    if not took:
        return m.get("restrictions"), False
    main_settings = [x for x in (m.get("settings") if isinstance(m.get("settings"), list) else [])
                     if isinstance(x, (dict, str))]
    if isinstance(m.get("setting"), dict):
        main_settings.append(m["setting"])
    rep_ctx = [x for x in rr if isinstance(x, dict) and x.get("kind") == RESTRICTION_CONTEXT]
    if not scope_ambiguous and not all(any(same_context(s_, c) for c in rep_ctx) for s_ in main_settings):
        return m.get("restrictions"), False              # a context the main answer stated would be lost
    return rr, True


def _merge_settings(m: dict, r: dict, sc: set[str], scope_ambiguous: bool) -> tuple[list | None, bool]:
    """-> (settings list to use, taken_from_repair). Never drops a context of the main answer."""
    ms = m.get("settings") if isinstance(m.get("settings"), list) else None
    rs = r.get("settings") if isinstance(r.get("settings"), list) else None
    if rs is None:
        return m.get("settings"), False
    if ms is None:
        legacy = m.get("setting")
        if not isinstance(legacy, dict):                 # the main list was unusable: nothing to preserve
            return copy.deepcopy(rs), True
        ms = [legacy]                                    # a v2 single setting is still a context to keep
    bad = {int(k[len(SCOPE_SETTING_PREFIX) + 1:]) for k in sc
           if k.startswith(SCOPE_SETTING_PREFIX) and k[len(SCOPE_SETTING_PREFIX) + 1:].isdigit()}
    if SCOPE_SETTINGS in sc:
        bad = set(range(len(ms)))
    if scope_ambiguous or contexts_preserved(ms, rs, bad):
        return copy.deepcopy(rs), True
    return ms, False


def _merge_restrictions(m: dict, r: dict, sc: set[str], scope_ambiguous: bool = False) -> tuple[list | None, bool]:
    """-> (list to use, taken_from_repair)."""
    mr = m.get("restrictions") if isinstance(m.get("restrictions"), list) else None
    rr = r.get("restrictions") if isinstance(r.get("restrictions"), list) else None
    if rr is None:
        return m.get("restrictions"), False
    if mr is None:                                       # the main list was unusable: nothing to preserve, but
        # P4b RC1: a repair is never the SOLE source of a context-only / vague / total-experience reading
        return (copy.deepcopy(rr), True) if _names_target(rr) else (m.get("restrictions"), False)
    bad = {int(k[len(SCOPE_RESTRICTION_PREFIX) + 1:]) for k in sc
           if k.startswith(SCOPE_RESTRICTION_PREFIX) and k[len(SCOPE_RESTRICTION_PREFIX) + 1:].isdigit()}
    if SCOPE_RESTRICTIONS in sc and not bad:
        bad = set(range(len(mr)))
    keep = [_jd_key(x, "kind") for j, x in enumerate(mr) if j not in bad]
    have = {_jd_key(x, "kind") for x in rr}
    # never broaden: every valid restriction kind of the main answer survives, and a non-empty main list is
    # never repaired into [] (= total experience), even when every main item was malformed
    kinds_main = {x.get("kind") for x in mr if isinstance(x, dict) and x.get("kind") in RESTRICTION_KINDS}
    kinds_rep = {x.get("kind") for x in rr if isinstance(x, dict)}
    # s1-6: a main entry of unknown kind (it may have been meant as a context) must reappear with the same text
    # (any kind); context entries must be preserved as contexts (contexts_preserved)
    rep_text = {_ctx_key(x) for x in rr}
    unknown = [_ctx_key(x) for x in mr if isinstance(x, dict) and x.get("kind") not in RESTRICTION_KINDS]
    main_ctx = [(j, x) for j, x in enumerate(mr) if isinstance(x, dict) and x.get("kind") == RESTRICTION_CONTEXT]
    ctx_ok = all(k is None or k in rep_text for k in unknown) and contexts_preserved(
        [x for _, x in main_ctx], [x for x in rr if isinstance(x, dict) and x.get("kind") == RESTRICTION_CONTEXT],
        {i for i, (j, _) in enumerate(main_ctx) if j in bad})
    if scope_ambiguous:                                  # contexts may go (needs_confirmation, no S2 view)
        kinds_main.discard(RESTRICTION_CONTEXT)
        keep = [k for k in keep if k is None or k[2] != RESTRICTION_CONTEXT]
        ctx_ok = True
    if (all(k in have for k in keep if k is not None) and kinds_main <= kinds_rep
            and (rr or not mr) and ctx_ok):
        return copy.deepcopy(rr), True
    return mr, False


def _jd_key(t, type_key: str = "type") -> tuple | None:
    if not isinstance(t, dict):
        return None
    return (t.get("line"), normalize(t.get("text") or ""), t.get(type_key))


def merge_repair(main_raw: str, repair_raw: str, scoped: list[ScopedError],
                 criteria: list[CriterionInput]) -> tuple[str, dict]:
    info: dict = {"mode": "scoped", "taken": [], "kept_main_restrictions": [], "discarded_changes": 0,
                  "pair_merged": [], "material_locked": []}
    main = _items(main_raw)
    if main is None or any(SCOPE_RESPONSE in e.scopes for e in scoped):
        info["mode"] = "full_replace"
        return repair_raw, info
    rep = _items(repair_raw)
    if rep is None:
        info["mode"] = "repair_unusable"
        return main_raw, info

    scopes: dict[str, set[str]] = defaultdict(set)
    for e in scoped:
        if e.criterion_id:
            scopes[e.criterion_id].update(e.scopes)

    out: list[dict] = []
    for c in criteria:
        cid = c.criterion_id
        m, r, sc = main.get(cid), rep.get(cid), scopes.get(cid, set())
        if not sc:
            if m is not None:
                out.append(m)
            if r is not None and r != m:
                info["discarded_changes"] += 1
            continue
        if m is None or SCOPE_CRITERION in sc:
            if r is not None:
                out.append(r)
                info["taken"].append({"criterion_id": cid, "field": "criterion"})
            elif m is not None:
                out.append(m)
            continue
        if r is None:
            out.append(m)
            continue

        merged = copy.deepcopy(m)
        for f in FIELD_SCOPES:
            if f in sc:
                merged[f] = copy.deepcopy(r.get(f))
                info["taken"].append({"criterion_id": cid, "field": f})
        amb_now = merged.get("ambiguity")
        scope_ambiguous = isinstance(amb_now, list) and AMB_AMBIGUOUS_CONTEXT_SCOPE in amb_now
        if hint_ids(c) and (SCOPE_SETTINGS in sc or any(x.startswith(SCOPE_SETTING_PREFIX) for x in sc)):
            merged["settings"], took = _merge_settings(m, r, sc, scope_ambiguous)
            if took:
                merged.pop("setting", None)              # the v2 single-setting key is never carried over
                info["taken"].append({"criterion_id": cid, "field": "settings"})
            else:
                info.setdefault("kept_main_settings", []).append(cid)
        tscopes = [s[len(SCOPE_TARGET_PREFIX):] for s in sc if s.startswith(SCOPE_TARGET_PREFIX)]
        tkeys = {k for k in tscopes if ":" not in k}
        tfields: dict[str, set[str]] = defaultdict(set)
        for k in tscopes:
            if ":" in k:
                key, f = k.split(":", 1)
                tfields[key].add(f)
        hints = hint_ids(c)
        if hints:
            if tkeys or tfields or SCOPE_TARGETS_EXTRA in sc:
                new = []
                for hid in hints:
                    rt, mt_ = _hint_target(r, hid), _hint_target(m, hid)
                    if hid in tkeys and rt is not None:
                        if all(_material_lock_holds(o, rt) for o in _hint_targets(m, hid)):
                            new.append(copy.deepcopy(rt))
                            info["taken"].append({"criterion_id": cid, "field": f"target:{hid}"})
                            continue
                        info["material_locked"].append({"criterion_id": cid, "hint": hid})
                        new += [copy.deepcopy(o) for o in _hint_targets(m, hid)]   # main kept: stays invalid
                        continue
                    if mt_ is None:
                        continue
                    t = copy.deepcopy(mt_)
                    local = _pair_local_issues(scoped, cid, hid)
                    if local is not None:
                        # s1-5.2.1: every error of this target is local to identified alignment pair(s): only
                        # those pairs may come from the repair; match, jd_span, jd_extra and valid pairs are kept
                        t["alignment"], replaced = _merge_pairs(mt_["alignment"], rt, local)
                        info["pair_merged"].append({"criterion_id": cid, "hint": hid,
                                                    "invalid": sorted(local), "replaced": replaced})
                        for k in replaced:
                            info["taken"].append({"criterion_id": cid, "field": f"target:{hid}:alignment[{k}]"})
                    elif rt is not None:
                        for f in sorted(tfields.get(hid, ())):
                            if f in rt:
                                t[f] = copy.deepcopy(rt[f])
                            else:
                                t.pop(f, None)
                            info["taken"].append({"criterion_id": cid, "field": f"target:{hid}:{f}"})
                    if not _material_lock_holds(mt_, t):
                        t = copy.deepcopy(mt_)                 # a material qualifier cannot be repaired away
                        info["material_locked"].append({"criterion_id": cid, "hint": hid})
                    new.append(t)
                if SCOPE_TARGETS_EXTRA in sc:     # the repair's own non-hint targets (if it kept any, still invalid)
                    rlist = r.get("targets") if isinstance(r.get("targets"), list) else []
                    new += [copy.deepcopy(t) for t in rlist if not (isinstance(t, dict) and t.get("hint") in hints)]
                    info["taken"].append({"criterion_id": cid, "field": "targets_extra"})
                merged["targets"] = new
        else:
            if SCOPE_TARGETS_EXTRA in sc:     # targets are derived from restrictions: only the repair's own list
                merged["targets"] = copy.deepcopy(r.get("targets"))
                info["taken"].append({"criterion_id": cid, "field": "targets_extra"})
            if (SCOPE_RESTRICTIONS in sc or any(x.startswith(SCOPE_RESTRICTION_PREFIX) for x in sc)
                    or SCOPE_SETTINGS in sc or any(x.startswith(SCOPE_SETTING_PREFIX) for x in sc)):
                merged["restrictions"], took = _merge_hintless(m, r, sc, scope_ambiguous)
                info["taken" if took else "kept_main_restrictions"].append(
                    {"criterion_id": cid, "field": "restrictions"} if took else cid)
                if took:                                 # P4b RC1: settings are never kept for a hint-less criterion
                    merged.pop("settings", None)
                    merged.pop("setting", None)
        if r != merged:
            info["discarded_changes"] += 1
        out.append(merged)
    return json.dumps({"criteria": out}, ensure_ascii=False), info
