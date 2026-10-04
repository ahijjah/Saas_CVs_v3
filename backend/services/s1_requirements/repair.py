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
from collections import defaultdict

from services.s1_requirements.criteria import CriterionInput
from services.s1_requirements.jd_text import normalize
from services.s1_requirements.schema import RESTRICTION_KINDS
from services.s1_requirements.validator import (
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
    info: dict = {"mode": "scoped", "taken": [], "kept_main_restrictions": [], "discarded_changes": 0}
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
                        new.append(copy.deepcopy(rt))
                        info["taken"].append({"criterion_id": cid, "field": f"target:{hid}"})
                        continue
                    if mt_ is None:
                        continue
                    t = copy.deepcopy(mt_)
                    if rt is not None:
                        for f in sorted(tfields.get(hid, ())):
                            if f in rt:
                                t[f] = copy.deepcopy(rt[f])
                            else:
                                t.pop(f, None)
                            info["taken"].append({"criterion_id": cid, "field": f"target:{hid}:{f}"})
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
