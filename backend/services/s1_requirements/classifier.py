"""
S1 classifier — ONE AI call per job labels every enumerated experience
criterion; deterministic validation and assembly do the rest. SHADOW ONLY.

  classify_job(job_id, jd_text, analysis_json, *, recruiter_fields=None,
               client=None, model=S1_MODEL, cache=None) -> S1JobResult

Input to the model (no candidate data, no job title, no domain_knowledge):
  numbered non-blank JD lines, duration candidates D1.. parsed from the JD,
  and per criterion: criterion_id, a neutral "kind", has_years, target hints
  T1.. (the analysis_json relevant_roles feeding it). The generated D-01
  display_text ("... in a relevant role (...)") is NOT sent (s1-4): it biased
  target typing; it stays unchanged in the recruiter-facing artifact.
  The model returns no policy: it is derived deterministically (validator).
  s1-6: the qualifying-context analysis (the experience qualifying-context object,
  its audit record, recruiter decisions on it) is NEVER read or sent: S1 is an
  independent reading of the JD; the agreement happens after S1.

Call flow (all-or-nothing per job; technical failures are never business states):
  no experience criteria             -> no call, no artifacts
  request over the input budget      -> failed_technical exceeds_model_context (cached)
  main call exception                -> failed_technical ai_unavailable (retryable; not cached)
  finish_reason == "length"          -> failed_technical output_truncated (not cached)
  invalid -> ONE repair call with the exact (scoped) violations; only the failed
             fields/targets are merged from it into the main answer
             (services.s1_requirements.repair; s1-6: a repair can never drop a
             context), then the merge is re-validated:
      over budget / exception / length -> failed_technical (as above)
      merged answer still invalid      -> failed_validation validation_failed (cached)
  valid                              -> per criterion resolved | needs_confirmation
  assembly invariant broken          -> failed_technical internal_error (retryable; not cached)

The cache stores the VALIDATED AI labels (or the cacheable failure), keyed by
the exact request; recruiter provenance is applied at assembly time, so it is
never part of the cached AI output.
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Protocol

from services.ai_service import _SECURITY_HARDENING_SUFFIX
from services.s0_experience import llm_call
from services.s1_requirements.assemble import assemble_artifact, check_recruiter_fields, failed_artifact
from services.s1_requirements.criteria import (
    KIND_ROLE_ONLY, KIND_YEARS_AND_ROLES, KIND_YEARS_ONLY, CriterionInput, enumerate_experience_criteria,
    out_of_scope_items,
)
from services.s1_requirements.durations import DurationMatch
from services.s1_requirements.jd_text import JDText
from services.s1_requirements.repair import (
    apply_withdrawal, merge_repair, normalize_duplicate_representations, pair_repair_guidance, plan_withdrawal,
)
from services.s1_requirements.schema import (
    REASON_AI_UNAVAILABLE, REASON_EXCEEDS_MODEL_CONTEXT, REASON_INTERNAL_ERROR, REASON_OUTPUT_TRUNCATED,
    REASON_VALIDATION_FAILED, S1_INPUT_VERSION, S1_MAX_TOKENS, S1_MODEL, S1_PROMPT_CODE, S1_PROMPT_VERSION,
    S1_TEMPERATURE, S1_VERSION, S1Artifact, canonical, sha256,
)
from services.s1_requirements.validator import ParsedCriterion, hint_ids, validate_response

logger = logging.getLogger(__name__)

S1_MAX_INPUT_TOKENS = llm_call.MAX_INPUT_TOKENS
CACHEABLE = frozenset({None, REASON_VALIDATION_FAILED, REASON_EXCEEDS_MODEL_CONTEXT})

S1_SYSTEM_PROMPT = """You label the EXPERIENCE requirements of ONE job description (JD). You never see or judge candidates.

INPUT (JSON): "jd_lines" (line number + verbatim text), "duration_candidates" (id, line, text: every duration found in the JD) and "criteria" (criterion_id, kind, has_years, target_hints with ids T1, T2, ...). kind ("years_with_targets", "years_only" or "single_target") only says how the criterion was built; it says nothing about whether a target is a role or a function. target_hints come from an earlier automatic extraction: they are fixed, and they are not proof that the JD says them.

For EVERY criterion return exactly one result. Never return a policy: it is computed from your answers.

1 REQUIREMENT_SPANS: the JD sentence(s) or bullet(s) that state THIS experience requirement, as [{"line": n, "text": "verbatim"}]. Quote the whole requirement statement; if it continues on the next line, quote each line. A separate sentence that restricts where THIS experience must have been gained (e.g. "All of this experience must have been gained in non-profit organisations.") is part of the requirement: quote it too and give its context (section 5). Never quote company descriptions, "about us", the location of this vacancy, duties/responsibilities of this job, other requirements (education, skills, licences, languages) or headings, even when they name a place, a sector or a kind of organisation. If the JD does not state this requirement, return [] and report "requirement_not_in_jd".
  Mark a span {"line": n, "text": "verbatim", "experience_requirement": true} ONLY when the quoted text itself states this experience requirement but contains neither its duration, nor a hint word for word, nor a phrase you map (e.g. "Experience in physiotherapy is preferred."). Never mark company descriptions or context (e.g. "We are a leading logistics group."). Such a criterion stays unconfirmed: never add a jd_span just to anchor a span.
  Match "none" for a target is NOT "requirement_not_in_jd": "none" means this supplied target is not stated (or equivalent) in the JD wording; "requirement_not_in_jd" means the JD states no such EXPERIENCE requirement at all. If the hint is a specific role but the JD only asks for experience in its field (e.g. hint "Physiotherapist", JD "Experience in physiotherapy is preferred."), the target is "none" and the requirement span stays, marked "experience_requirement": true.

2 TARGETS (criteria WITH target_hints only)
  a. Return exactly ONE target object for each supplied hint id: never output the same hint twice for different types or matches. Choose one type; if you are genuinely unsure whether it is a role or a function, use the ambiguity rule in b, never a second object. Each object is {"hint": "T1", "type": "role" | "function", "match": "exact" | "equivalent" | "none", "jd_span": null | {"line": n, "text": "verbatim"}} (plus "alignment" and "jd_extra" for "equivalent", section 3). Never drop, rename, merge, split or add targets, and never rewrite the hint text itself. Recording the JD's own wording for a hint in jd_span is not a rewrite.
  b. TYPE: classify the target words themselves.
     "role"      a position the candidate HOLDS or IS: a title naming a person (e.g. Payroll Officer, Translator, Laboratory Technician, Marketing Manager).
     "function"  work, a discipline, field or activity the candidate DOES or WORKS IN, whatever their title (e.g. payroll administration, translation, laboratory testing, marketing).
     Test: if "she is a <target>" makes sense, it is a role; if "experience in <target>" or "doing <target>" names work rather than a person, it is a function. The same subject can be either: "Translator" is a role, "translation" is a function.
     Nothing in the input tells you the type (not kind, not where a hint came from); decide it from the target words and the JD.
     If the type cannot be decided, use "function" and report "ambiguous_relevance".

3 MATCH, JD_SPAN and ALIGNMENT, for each hint:
  "exact"       the complete hint appears word for word inside this criterion's requirement_spans as the role/function itself. jd_span = null (the code verifies it).
  "equivalent"  a verbatim phrase inside this criterion's requirement_spans names SUBSTANTIALLY THE SAME role or function as the hint, and the ONLY differences are language (translation), grammatical form or a legitimate abbreviation. jd_span = that phrase (REQUIRED), with "alignment" and "jd_extra" (REQUIRED).
  "none"        everything else: absent, broader, narrower, adjacent, related, compatible, qualifier-changing, or uncertain. jd_span = null. If the hint appears word for word only inside a longer phrase that adds a material qualifier (e.g. "Translator" inside "Legal Translator"), use "none" and report "ambiguous_relevance".
  ALIGNMENT for "equivalent": account for EVERY word on both sides.
    "alignment": one pair per word of the hint: {"hint": "<ONE word of the hint>", "jd": "<the verbatim JD word(s) in jd_span that say that same word>", "relation": "same" | "form" | "translation" | "abbreviation"}.
      same          letter for letter the same word, in the same language (case, and the periods of a dotted acronym such as "Q.A." / "QA", do not count).
      form          the SAME word in another grammatical form, same language, one word to one word: plural (account / accounts) or verb/noun form (control / controlling). Never a synonym or a different word with a related, broader or narrower meaning: two different words are never a "form" of each other. A mapping that needs a form pair is kept only as a candidate for recruiter confirmation, so use form only when it is truly the same word.
      translation   EVERY pair between two languages, even when the meaning is identical (Warehouse / مستودع); may be several JD words.
      abbreviation  an all-capitals acronym and its expansion (HR / Human Resources); the only pair that may hold several hint words.
    A JD word may be given with or without the Arabic letters attached to its start (ك, ب, ل, و, ف): "مستودع" for "كمستودع" is fine; nothing else may be cut from a word.
    Name a role ONCE: if the JD gives both the full form and its acronym (e.g. "Quality Assurance (Q.A.)"), jd_span is ONE of them, preferably the full form; never a span holding both.
    "jd_extra": every other word of jd_span, each as {"text": "<one word>", "kind": "grammatical" | "material"}. grammatical = carries no requirement meaning (of, the, and, في, ...). material = any word that adds meaning (a sector, industry, technology, platform, project type, seniority, specialisation, scope or context). ANY material word means the JD phrase is NOT the same role/function: use "none" instead.
    A hint word with no JD counterpart means the JD phrase drops it: use "none". Never pair a hint word with a JD word that does not say the same thing.
  Illustrations (these are not the hints you will see):
    "Hospital Pharmacist" -> jd_span "كصيدلي مستشفى": equivalent, alignment [{"hint": "Hospital", "jd": "مستشفى", "relation": "translation"}, {"hint": "Pharmacist", "jd": "كصيدلي", "relation": "translation"}], jd_extra [].
    "inventory control" -> jd_span "controlling inventory": equivalent, alignment [{"hint": "inventory", "jd": "inventory", "relation": "same"}, {"hint": "control", "jd": "controlling", "relation": "form"}], jd_extra [].
    "HR Manager" -> jd_span "Human Resources Manager": equivalent, alignment [{"hint": "HR", "jd": "Human Resources", "relation": "abbreviation"}, {"hint": "Manager", "jd": "Manager", "relation": "same"}], jd_extra [].
    "Hospital Pharmacist" -> "كصيدلي": none ("Hospital" has no counterpart). "inventory control" -> "controlling cold-storage inventory": none ("cold", "storage" are material). "laboratory testing" -> "laboratory equipment maintenance": none (adjacent function). "Marketing Manager" -> "Marketing Coordinator": none (different role level). "Translator" -> "working with the translation team": none (works with the target, does not hold it).
  - Copy whole words exactly as written. Arabic letters attached to the start of a word (ك "as", ب, ل, و, ف) are not qualifiers: the span may include them or start right after them.
  - Never map to the duration, a context (section 5), the same employer, sector, project or industry, a transferable skill, general relevance, or text outside this criterion's requirement_spans.
  - An abbreviation is equivalent only when the requirement span itself makes its meaning unambiguous.
  - A phrase may be the jd_span of at most one hint; if two hints seem to match the same phrase, use "none" for both.
  - If you are not certain the meaning is the same, use "none". "none" is always acceptable; a wrong "equivalent" is not.
  - The hint text stays exactly as supplied whatever the match.

4 RESTRICTIONS (criteria WITHOUT target_hints only; omit for criteria with target_hints; never return targets or settings for them):
  "restrictions": EVERY phrase in the requirement statement that limits which experience counts, each as {"line": n, "text": "verbatim", "kind": "role" | "function" | "context" | "vague"}.
    role      a position the candidate must have held (e.g. "as a Laboratory Technician" -> "Laboratory Technician").
    function  work, a field or a discipline (e.g. "payroll administration experience" -> "payroll administration").
    context   WHERE or IN WHAT SETTING the experience must have been gained, exactly as defined in section 5 (e.g. "in the telecommunications sector" -> "telecommunications sector"). Every rule of section 5 applies.
    vague     "relevant", "related", "similar", "in the field" or the like WITHOUT saying relevant to what.
  Alternatives are separate restrictions ("as a Laboratory Technician or in laboratory testing" -> one role and one function).
  A role or function and a context are separate phrases that never overlap ("as a Laboratory Technician in public hospitals" -> role "Laboratory Technician" and context "public hospitals").
  Return [] ONLY when the requirement asks for general, overall or professional experience with no restriction at all (e.g. "4 years of professional experience"). Leaving out a restriction silently broadens the requirement.
  Do not include the duration in a restriction.

5 SETTINGS = EXPERIENCE CONTEXTS (criteria WITH target_hints: "settings"; criteria without target_hints: "context" restrictions, section 4).
  A context is a phrase restricting WHERE or IN WHAT SETTING otherwise relevant past experience must have been gained: someone with the same role or function and enough years, gained outside it, would NOT meet the requirement. Kinds of context:
    geographic scope      (e.g. "in the Nordic countries")
    organisation type     (e.g. "in non-profit organisations", "in state-owned utilities")
    sector or domain      (e.g. "telecommunications sector", "public hospitals")
    project type          (e.g. "on railway projects")
    work setting          (e.g. "pharmaceutical manufacturing plants")
  "settings": a list of 0 to 5 contexts, each {"line": n, "text": "verbatim"}, INSIDE this criterion's requirement_spans; [] when the requirement has no context. Copy the phrase naming the context; a leading "in", "on", "within", "the", "في", "ضمن" or "لدى" may be included or left out; never include the role, the function or the duration.
  - ONE contiguous restriction is ONE context, even when it names several things: "in state-owned utilities across the Nordic countries" is ONE context. Never split it.
  - Restrictions stated SEPARATELY are separate contexts, and ALL of them must hold for the same past job (e.g. "... in the telecommunications sector." and "All of this experience must have been gained in non-profit organisations." -> two contexts).
  - "or" inside a context stays inside it: "in hospitals or clinics" is ONE context. Never split an "or".
  - NEVER a context: the hiring company's name or description ("About us", "We are a ... group"); the location of this vacancy ("Location: ...", "based in ..."); duties or responsibilities of this job; seniority; tools or technologies; generic adjectives, culture or working environment ("dynamic", "fast-paced", "multicultural team", "challenging environment"); and "multinational", "international" or "global" when they describe the hiring company or its team rather than where the candidate's past experience was gained. Never take a context from text that does not state this experience requirement, or from the job title.
  - Do not give a context that is already part of a target (e.g. "Hospital" in "Hospital Pharmacist").
  - When a whole experience requirement is preferred ("Experience ... in X is preferred / an advantage"), X is still its context.
  - AMBIGUOUS SCOPE: if a context restricts only some alternatives of the criterion (e.g. "as a Surveyor in mining companies or as a Cartographer"), or is only softened on a firm requirement ("minimum N years as R, preferably / ideally in X"), give NO context for it ("settings": [], or no "context" restriction) and report "ambiguous_context_scope". Never apply it to every alternative and never drop it silently.
  - PART DURATION: if a context applies only to a part of the experience that has its own duration (e.g. "6 years overall, including 2 years in ..."), give NO context for it ("settings": [], or no "context" restriction) and do NOT report "ambiguous_context_scope" for this: keep the whole statement in requirement_spans (section 6); the code records it as a compound requirement.

6 DURATION: null, or the id of the duration candidate inside this criterion's requirement_spans that states its minimum experience. Never compute or restate a number. If more than one candidate could be this criterion's minimum, return null and report "multiple_durations". If the statement sets several thresholds (e.g. an overall minimum and a minimum in one role), keep the whole statement in requirement_spans; the code records it as a compound requirement.

7 AMBIGUITY: [] or any of:
  "ambiguous_relevance"       what counts as relevant, a target's type, or a target embedded in a longer qualified phrase is unclear;
  "ambiguous_context_scope"   a context exists but it is unclear which alternatives of the requirement it restricts, or it is only softened (section 5); then it is not given as a context. Never for a part of the experience with its own duration (that is a compound requirement);
  "multiple_durations"        more than one duration could be this criterion's minimum;
  "conflicting_requirements"  the JD itself states this experience requirement in materially different ways (e.g. a different minimum, or a different role, in two places). A hint that differs from the JD wording is NOT a conflict: that is match "none";
  "requirement_not_in_jd"     the JD does not state this requirement (then requirement_spans is []).
  Report ambiguity instead of guessing; never broaden a requirement.

8 NOTE: one short sentence.

OUTPUT: JSON only, e.g.:
{"criteria": [
 {"criterion_id": "...", "requirement_spans": [{"line": 7, "text": "verbatim"}],
  "targets": [{"hint": "T1", "type": "function", "match": "exact", "jd_span": null}],
  "settings": [], "duration": "D1", "ambiguity": [], "note": "one sentence"},
 {"criterion_id": "...", "requirement_spans": [{"line": 9, "text": "verbatim"}],
  "targets": [{"hint": "T1", "type": "role", "match": "equivalent", "jd_span": {"line": 9, "text": "verbatim phrase"},
               "alignment": [{"hint": "word", "jd": "verbatim word(s)", "relation": "translation"}], "jd_extra": []}],
  "settings": [{"line": 9, "text": "verbatim context"}], "duration": "D2", "ambiguity": [], "note": "one sentence"},
 {"criterion_id": "...", "requirement_spans": [{"line": 11, "text": "verbatim"}],
  "restrictions": [{"line": 11, "text": "verbatim", "kind": "function"}, {"line": 11, "text": "verbatim", "kind": "context"}],
  "duration": "D3", "ambiguity": [], "note": "one sentence"}]}""" + _SECURITY_HARDENING_SUFFIX


def prompt_fingerprint() -> str:
    return sha256(S1_SYSTEM_PROMPT)[:12]


class S1Cache(Protocol):
    def get(self, key: str) -> dict | None: ...
    def set(self, key: str, value: dict) -> None: ...


class InMemoryS1Cache:
    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    def get(self, key: str) -> dict | None:
        return self.store.get(key)

    def set(self, key: str, value: dict) -> None:
        self.store[key] = value


@dataclass
class BuiltRequest:
    payload: dict
    jd: JDText
    criteria: list[CriterionInput]
    durations: dict[str, tuple[int, DurationMatch]]

    @property
    def user_message(self) -> str:
        return "INPUT:\n" + canonical(self.payload)

    @property
    def input_hash(self) -> str:
        return sha256(self.user_message)


# neutral criterion kinds sent to the model (the generated display_text is never sent)
INPUT_KINDS = {KIND_YEARS_AND_ROLES: "years_with_targets", KIND_YEARS_ONLY: "years_only",
               KIND_ROLE_ONLY: "single_target"}


def build_request(jd: JDText, criteria: list[CriterionInput]) -> BuiltRequest:
    durs = {did: (ln, m) for did, ln, m in jd.durations()}
    payload = {
        "s1_input_version": S1_INPUT_VERSION,
        "jd_lines": jd.numbered(),
        "duration_candidates": [{"id": did, "line": ln, "text": m.text} for did, (ln, m) in durs.items()],
        "criteria": [{"criterion_id": c.criterion_id, "kind": INPUT_KINDS[c.kind], "has_years": c.has_years,
                      "target_hints": [{"id": hid, "text": t} for hid, t in hint_ids(c).items()]}
                     for c in criteria],
    }
    return BuiltRequest(payload, jd, criteria, durs)


def s1_cache_key(req: BuiltRequest, *, model: str = S1_MODEL) -> str:
    return sha256(f"s1|{S1_VERSION}|{req.input_hash}|{S1_PROMPT_VERSION}:{prompt_fingerprint()}|{model}")


def repair_note(errors: list[str], pair_guidance: list[str] = ()) -> str:
    pairs = ("\nPair-level repair (s1-5.2.1):\n- " + "\n- ".join(pair_guidance)) if pair_guidance else ""
    return ("Some fields of your previous JSON violate the rules below. Return the COMPLETE JSON again with "
            "exactly one result for every criterion. ONLY the fields, targets and restrictions named in these "
            "errors will be taken from your new answer; everything else is kept exactly as in your previous "
            "answer, so fix these and change nothing else. Quote JD text verbatim and never add, drop or rewrite "
            "targets. Never drop a context (settings or a \"context\" restriction): that would broaden the "
            "requirement. A jd_span is only for match \"equivalent\" (the SAME role/function in different wording, "
            "with an alignment accounting for every word); when in doubt use match \"none\". Never drop a "
            "restriction: that would broaden the requirement. Exactly ONE object per hint id. Relations: "
            "same = letter for letter the same word; form = the same word in another grammatical form of the "
            "same language (plural, verb/noun form), never a synonym; translation = EVERY pair between two "
            "languages; abbreviation = acronym and expansion. Never return a policy:\n- "
            + "\n- ".join(errors[:40]) + pairs)


@dataclass
class S1JobResult:
    job_id: str
    status: str                     # "ok" | "failed" | "empty"
    status_reason: str | None
    artifacts: list[S1Artifact] = field(default_factory=list)
    out_of_scope: list[dict] = field(default_factory=list)
    cache_key: str | None = None
    meta: dict = field(default_factory=dict)
    validation: dict = field(default_factory=lambda: {"errors": [], "repair_errors": []})


_client = None


def _get_client():
    global _client
    if _client is None:
        _client = llm_call.create_client()
    return _client


def _serialise_parsed(results: dict[str, ParsedCriterion]) -> dict:
    return {cid: {"policy": p.policy, "requirement_spans": [s.to_dict() for s in p.requirement_spans],
                  "targets": [{"text": t.text, "type": t.type, "hint_id": t.hint_id,
                               "span": t.span.to_dict() if t.span else None, "match": t.match,
                               "alignment": [dict(x) for x in t.alignment],
                               "jd_extra": [dict(x) for x in t.jd_extra]}
                              for t in p.targets],
                  "restrictions": [{"text": r.text, "kind": r.kind, "span": r.span.to_dict()}
                                   for r in p.restrictions],
                  "anchor_kind": p.anchor_kind, "withdrawn": [dict(x) for x in p.withdrawn],
                  "normalized": [dict(x) for x in p.normalized],
                  "settings": [x.to_dict() for x in p.settings], "duration": p.duration_id,
                  "ambiguity": list(p.ambiguity), "note": p.note,
                  "statement_anchored": p.statement_anchored, "relevance_basis": p.relevance_basis,
                  "policy_derivation": p.policy_derivation, "model_policy": p.model_policy}
            for cid, p in results.items()}


def _deserialise_parsed(d: dict) -> dict[str, ParsedCriterion]:
    from services.s1_requirements.schema import Span
    from services.s1_requirements.validator import ParsedRestriction, ParsedTarget
    return {cid: ParsedCriterion(
        cid, p["policy"], tuple(Span.from_dict(s) for s in p["requirement_spans"]),
        tuple(ParsedTarget(t["text"], t["type"], t.get("hint_id"), Span.from_dict(t.get("span")), t.get("match"),
                           tuple(t.get("alignment") or ()), tuple(t.get("jd_extra") or ()))
              for t in p["targets"]),
        tuple(Span.from_dict(x) for x in p.get("settings") or ()), p.get("duration"), tuple(p.get("ambiguity") or ()), p.get("note", ""),
        bool(p.get("statement_anchored")), p.get("relevance_basis"), p.get("policy_derivation", "from_types"),
        p.get("model_policy"),
        tuple(ParsedRestriction(r["text"], r["kind"], Span.from_dict(r["span"])) for r in p.get("restrictions") or ()),
        p.get("anchor_kind", "evidence"), tuple(p.get("withdrawn") or ()), tuple(p.get("normalized") or ()))
        for cid, p in d.items()}


async def classify_job(job_id: str, jd_text: str, analysis_json: dict | None, *,
                       recruiter_fields: Mapping[str, str] | None = None, client: Any = None,
                       model: str = S1_MODEL, cache: S1Cache | None = None) -> S1JobResult:
    rf = check_recruiter_fields(recruiter_fields)
    criteria = enumerate_experience_criteria(job_id, analysis_json)
    oos = out_of_scope_items(analysis_json)
    if not criteria:
        return S1JobResult(str(job_id), "empty", None, out_of_scope=oos)
    jd = JDText(jd_text)
    req = build_request(jd, criteria)
    key = s1_cache_key(req, model=model)
    meta: dict[str, Any] = {
        "prompt_code": S1_PROMPT_CODE, "prompt_version": S1_PROMPT_VERSION,
        "prompt_fingerprint": prompt_fingerprint(), "model": model, "temperature": S1_TEMPERATURE,
        "max_output_tokens": S1_MAX_TOKENS, "max_input_tokens": S1_MAX_INPUT_TOKENS,
        "token_count_method": llm_call.TOKEN_COUNT_METHOD, "input_hash": req.input_hash,
        "request_token_upper_bound": None, "repair_request_token_upper_bound": None,
        "calls": 0, "repair_used": False, "call_log": [], "cache_hit": False,
    }

    outcome = cache.get(key) if cache is not None else None
    if outcome is not None:
        meta = {**outcome["meta"], "cache_hit": True}
    else:
        outcome = await _run(req, client, model, meta)
        if cache is not None and outcome["reason"] in CACHEABLE:
            cache.set(key, copy.deepcopy(outcome))

    run = {"input_hash": req.input_hash, "prompt_version": S1_PROMPT_VERSION,
           "prompt_fingerprint": prompt_fingerprint(), "model": model,
           "call": {k: meta.get(k) for k in ("calls", "repair_used", "outcome", "cache_hit")}}
    validation = outcome["validation"]
    res = S1JobResult(str(job_id), "failed" if outcome["reason"] else "ok", outcome["reason"],
                      out_of_scope=oos, cache_key=key, meta=meta, validation=validation)
    if outcome["reason"]:
        res.artifacts = [failed_artifact(c, outcome["reason"], run=run, validation=validation,
                                         recruiter_fields=rf) for c in criteria]
        return res
    try:
        parsed = _deserialise_parsed(outcome["parsed"])
        res.artifacts = [assemble_artifact(c, parsed[c.criterion_id], jd, req.durations, run=run,
                                           recruiter_fields=rf, validation=validation) for c in criteria]
    except Exception as exc:                    # assembly invariant broken = bug, never a business state
        logger.exception("S1 assembly failed")
        meta["error"] = f"{type(exc).__name__}: {exc}"[:300]
        res.status, res.status_reason = "failed", REASON_INTERNAL_ERROR
        res.artifacts = [failed_artifact(c, REASON_INTERNAL_ERROR, run=run, validation=validation,
                                         recruiter_fields=rf) for c in criteria]
    return res


def _claim_source(merge_info: dict, rec: dict) -> str:
    """"repair" when the merge took this target's semantic claim (its match or jd_span, the whole target, the whole
    criterion or the whole answer) from the repair answer; otherwise "main". Read from the merge record only."""
    if merge_info.get("mode") == "full_replace":
        return "repair"
    hid = rec["hint"]
    claim = {f"target:{hid}", f"target:{hid}:match", f"target:{hid}:jd_span", "criterion"}
    return "repair" if any(x.get("criterion_id") == rec["criterion_id"] and x.get("field") in claim
                           for x in merge_info.get("taken", [])) else "main"


async def _run(req: BuiltRequest, client, model: str, meta: dict) -> dict:
    def _out(reason, validation=None, parsed=None):
        return {"reason": reason, "parsed": parsed, "meta": meta,
                "validation": validation or {"errors": [], "repair_errors": []}}

    messages = [{"role": "system", "content": S1_SYSTEM_PROMPT},
                {"role": "user", "content": req.user_message}]
    bound = llm_call.request_token_upper_bound(messages)
    meta["request_token_upper_bound"] = bound
    if bound > S1_MAX_INPUT_TOKENS:
        return _out(REASON_EXCEEDS_MODEL_CONTEXT)
    client = client or _get_client()
    first_errors: list[str] = []
    try:
        raw, finish = await llm_call.chat_json_call(
            client, model=model, messages=messages, temperature=S1_TEMPERATURE,
            max_tokens=S1_MAX_TOKENS, call_log=meta["call_log"], kind="main")
        meta["calls"] = 1
        if finish == "length":
            return _out(REASON_OUTPUT_TRUNCATED)
        val = validate_response(raw, req.jd, req.criteria, req.durations)
        outcome = "validated"
        normalized: list[dict] = []
        if not val.ok:
            # s1-5.2.2: deterministic duplicate-representation span narrowing BEFORE any repair call
            narrowed, normalized = normalize_duplicate_representations(val.scoped, raw, req.criteria)
            normalized = [{**r, "stage": "pre_repair", "claim_source": "main"} for r in normalized]
            if normalized:
                main_errors = list(val.errors)
                raw, val = narrowed, validate_response(narrowed, req.jd, req.criteria, req.durations)
                meta["span_normalized"] = normalized
                if val.ok:
                    first_errors, outcome = main_errors, "normalized"
        if not val.ok:
            first_errors, first_scoped, main_raw = list(val.errors), list(val.scoped), raw
            repair_messages = messages + [{"role": "assistant", "content": raw},
                                          {"role": "user", "content": repair_note(
                                              first_errors, pair_repair_guidance(first_scoped))}]
            rb = llm_call.request_token_upper_bound(repair_messages)
            meta["repair_request_token_upper_bound"] = rb
            if rb > S1_MAX_INPUT_TOKENS:
                return _out(REASON_EXCEEDS_MODEL_CONTEXT, {"errors": first_errors, "repair_errors": []})
            meta["repair_used"] = True
            raw, finish = await llm_call.chat_json_call(
                client, model=model, messages=repair_messages, temperature=S1_TEMPERATURE,
                max_tokens=S1_MAX_TOKENS, call_log=meta["call_log"], kind="repair")
            meta["calls"] = 2
            if finish == "length":
                return _out(REASON_OUTPUT_TRUNCATED, {"errors": first_errors, "repair_errors": []})
            try:
                merged, meta["repair_merge"] = merge_repair(main_raw, raw, first_scoped, req.criteria)
            except Exception as exc:            # deterministic merge failed = bug, never an AI outage
                logger.exception("S1 repair merge failed")
                meta["error"] = f"{type(exc).__name__}: {exc}"[:300]
                return _out(REASON_INTERNAL_ERROR, {"errors": first_errors, "repair_errors": []})
            val = validate_response(merged, req.jd, req.criteria, req.durations)
            outcome = "repaired"
            if not val.ok:
                # s1-5.2.2.1: the SAME duplicate-representation narrowing, once, on the protected merged answer
                # (after the merge rules and the material lock, before withdrawal); no second repair call
                narrowed, post = normalize_duplicate_representations(val.scoped, merged, req.criteria)
                if post:
                    taken = meta["repair_merge"]
                    post = [{**r, "stage": "post_repair", "claim_source": _claim_source(taken, r)} for r in post]
                    merged, val = narrowed, validate_response(narrowed, req.jd, req.criteria, req.durations)
                    normalized = normalized + post
                    meta["span_normalized"] = normalized
                    if val.ok:
                        outcome = "repaired_normalized"
            withdrawn: dict[str, dict] = {}
            if not val.ok:
                # s1-5.1: the only fallback after the one repair: withdraw a lone failing equivalent claim
                plan = plan_withdrawal(val.scoped, merged, req.criteria)
                if plan:
                    narrowed, withdrawn = apply_withdrawal(merged, plan)
                    wval = validate_response(narrowed, req.jd, req.criteria, req.durations)
                    if wval.ok:
                        val, outcome = wval, "repaired_withdrawn"
                        val.results = {cid: replace(p, withdrawn=(withdrawn[cid],)) if cid in withdrawn else p
                                       for cid, p in val.results.items()}
                        meta["alignment_withdrawn"] = [{"criterion_id": cid, **w} for cid, w in withdrawn.items()]
            if not val.ok:
                return _out(REASON_VALIDATION_FAILED, {"errors": first_errors, "repair_errors": list(val.errors)})
    except Exception as exc:                    # network / API / auth / timeout
        logger.warning("S1 classifier unavailable: %s", exc)
        meta["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return _out(REASON_AI_UNAVAILABLE, {"errors": first_errors, "repair_errors": []})
    meta["outcome"] = outcome
    results = val.results
    for rec in normalized:                      # audit only where the final answer kept the narrowed span
        p = results.get(rec["criterion_id"])
        t = next((t for t in p.targets if t.hint_id == rec["hint"]), None) if p else None
        if t is not None and t.span is not None and t.span.text == rec["normalized_span"]:
            results[rec["criterion_id"]] = replace(p, normalized=p.normalized + (rec,))
    return _out(None, {"errors": first_errors, "repair_errors": []}, _serialise_parsed(results))


__all__ = ["classify_job", "build_request", "S1JobResult", "InMemoryS1Cache", "s1_cache_key",
           "S1_SYSTEM_PROMPT", "prompt_fingerprint", "repair_note"]
