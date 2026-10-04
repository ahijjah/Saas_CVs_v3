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
  target:Jk / targets_if_empty     -> hint-less target list taken from the repair
                                      ONLY if every error-free main target is
                                      kept unchanged (line, text, type) in it;
                                      otherwise the main list is kept
  "response" (unparseable / malformed main answer, unknown criterion id)
                                   -> the repair answer is taken as a whole
"""
from __future__ import annotations

import copy
import json
from collections import defaultdict

from services.s1_requirements.criteria import CriterionInput
from services.s1_requirements.jd_text import normalize
from services.s1_requirements.validator import (
    SCOPE_AMBIGUITY, SCOPE_BASIS, SCOPE_CRITERION, SCOPE_DURATION, SCOPE_RESPONSE, SCOPE_SETTING, SCOPE_SPANS,
    SCOPE_TARGET_PREFIX, SCOPE_TARGETS_EXTRA, SCOPE_TARGETS_IF_EMPTY, ScopedError, hint_ids,
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


def _hint_target(item: dict, hid: str) -> dict | None:
    targets = item.get("targets")
    for t in targets if isinstance(targets, list) else []:
        if isinstance(t, dict) and t.get("hint") == hid:
            return t
    return None


def _jd_key(t) -> tuple | None:
    if not isinstance(t, dict):
        return None
    return (t.get("line"), normalize(t.get("text") or ""), t.get("type"))


def merge_repair(main_raw: str, repair_raw: str, scoped: list[ScopedError],
                 criteria: list[CriterionInput]) -> tuple[str, dict]:
    info: dict = {"mode": "scoped", "taken": [], "kept_main_targets": [], "discarded_changes": 0}
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
            mt = m.get("targets") if isinstance(m.get("targets"), list) else []
            if tkeys or (SCOPE_TARGETS_IF_EMPTY in sc and not mt):
                rt = r.get("targets") if isinstance(r.get("targets"), list) else []
                bad = {int(k[1:]) for k in tkeys if k[:1] == "J" and k[1:].isdigit()}
                keep = [_jd_key(t) for j, t in enumerate(mt) if j not in bad]
                have = {_jd_key(t) for t in rt}
                if all(k in have for k in keep if k is not None):
                    merged["targets"] = copy.deepcopy(rt)
                    info["taken"].append({"criterion_id": cid, "field": "targets"})
                else:
                    info["kept_main_targets"].append(cid)
        if r != merged:
            info["discarded_changes"] += 1
        out.append(merged)
    return json.dumps({"criteria": out}, ensure_ascii=False), info
