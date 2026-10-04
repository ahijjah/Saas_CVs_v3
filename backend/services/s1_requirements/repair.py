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
  requirement_spans / setting / duration / ambiguity / relevance_basis
                                   -> that field taken from the repair
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
from services.s1_requirements.schema import RESTRICTION_KINDS
from services.s1_requirements.validator import (
    UNIT_PAIR, tokens,
    SCOPE_AMBIGUITY, SCOPE_BASIS, SCOPE_CRITERION, SCOPE_DURATION, SCOPE_RESPONSE, SCOPE_SETTING, SCOPE_SPANS,
    SCOPE_RESTRICTION_PREFIX, SCOPE_RESTRICTIONS, SCOPE_TARGET_PREFIX, SCOPE_TARGETS_EXTRA,
    ScopedError, hint_ids,
)

FIELD_SCOPES = (SCOPE_SPANS, SCOPE_SETTING, SCOPE_DURATION, SCOPE_AMBIGUITY, SCOPE_BASIS)


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


def _merge_restrictions(m: dict, r: dict, sc: set[str]) -> tuple[list | None, bool]:
    """-> (list to use, taken_from_repair)."""
    mr = m.get("restrictions") if isinstance(m.get("restrictions"), list) else None
    rr = r.get("restrictions") if isinstance(r.get("restrictions"), list) else None
    if rr is None:
        return m.get("restrictions"), False
    if mr is None:                                       # the main list was unusable: nothing to preserve
        return copy.deepcopy(rr), True
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
    if (all(k in have for k in keep if k is not None) and kinds_main <= kinds_rep
            and (rr or not mr)):
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
            if SCOPE_RESTRICTIONS in sc or any(x.startswith(SCOPE_RESTRICTION_PREFIX) for x in sc):
                merged["restrictions"], took = _merge_restrictions(m, r, sc)
                info["taken" if took else "kept_main_restrictions"].append(
                    {"criterion_id": cid, "field": "restrictions"} if took else cid)
        if r != merged:
            info["discarded_changes"] += 1
        out.append(merged)
    return json.dumps({"criteria": out}, ensure_ascii=False), info
