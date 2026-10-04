"""
S1 deterministic validator for the AI classifier output (prompt s1-5, S1 1.4.0).

The AI may only: type the analysis_json target hints (role | function), judge
their match (exact | equivalent | none), select verbatim JD spans (requirement
spans, targets for hint-less criteria, a criterion-specific setting, a hint's
equivalent wording), state the relevance basis of a hint-less criterion,
choose a duration candidate id and report ambiguity codes. Everything it
returns is verified here; nothing is coerced. The POLICY is never taken from
the model: it is derived here (see "Policy derivation").

Every error is SCOPED (criterion + field or target) so that the single repair
call can be merged field-by-field into the main answer (services.s1_requirements
.repair): valid primitives are never rewritten by a repair.

Rules
  R1  JSON object {"criteria": [...]}, exactly one result per criterion_id.
  R2  ambiguity codes in AMBIGUITY_CODES (no duplicates). A "policy" key from the
      model is ignored (recorded as model_policy for diagnostics only).
  R3  requirement_spans: [{line, text}], text >= 3 chars, verbatim (comparison
      form, whole words; Arabic proclitics allowed, see jd_text) on that JD line;
      the same holds for every span below.
      Empty  <=>  ambiguity contains requirement_not_in_jd.
  R4  hint criteria: targets are exactly the hints, each once, as {"hint": "Tn",
      "type", "match", "jd_span"}; a returned "text" must equal the hint text
      exactly (no rewrite); no JD-span targets may be added (that would broaden
      the OR set). A "jd_span" maps the hint to the verbatim JD text stating it;
      it must lie inside one of the criterion's requirement spans. The hint text
      itself is never replaced by the mapped span.
  R5  hint-less criteria: no targets (derived from restrictions, see below).
  R6  setting: null or {"line", "text"} verbatim JD span inside one of the
      criterion's requirement spans. No free text, no analysis_json source.
  R7  duration: null or a duration candidate id that lies inside one of the
      criterion's requirement spans; only for criteria with years.

  Policy derivation (deterministic, s1-4):
    hint criteria:   all role -> explicit_role; all function -> functional;
                     role + function -> mixed. relevance_basis must be absent
                     or "targets".
    hint-less criteria (s1-5) return typed "restrictions" (verbatim spans inside
    the requirement spans; kind role | function | sector | vague) and NO targets
    or setting; the relevance basis is derived, never claimed:
      role/function restrictions -> JD-derived targets (type = kind, jd_asserted,
                                    J1..); policy from their types; a single
                                    sector restriction becomes their setting
      else one sector restriction -> setting; policy sector
      else vague restriction      -> pure_duration + ambiguous_relevance
                                    (needs_confirmation; added by the assembler)
      else no restriction         -> total experience; pure_duration
    "total experience" is therefore only the derived absence of restrictions.
  V-align (s1-5, "equivalent" only): word alignment with full structural
    coverage, see _alignment_errors. s1-5.1: all word-level checks use the
    canonical jd_text words (a dotted acronym is one word) and the span
    boundary rule (only the approved Arabic proclitic chain may stay in front
    of a word); a jd_span holding both a role's full form and its acronym is
    rejected ("names the same role twice").

  s1-2 target-mapping guards (span/string checks only; semantic equivalence is
  the AI's judgment, audited, never decided here):
  V-M8   a mapped jd_span has at most 12 words and does not overlap the selected
         duration span or the setting span.
  V-M9   mapped jd_spans of different hints do not overlap.
  V-M10  a mapped jd_span does not overlap another hint's verbatim match inside
         the criterion's requirement spans.
  V-sub-a  a mapping is not a proper whole-word sub-phrase of its target
           ("Construction Project Manager" -> "Project Manager" fails).
  V-sub-b  a mapping does not contain its target as a proper whole-word
           sub-phrase ("software implementation" -> "enterprise software
           implementation" fails).
  s1-3 target match (hint targets only; consistency checks, no semantics):
  V-match  "match" is required and one of exact | equivalent | none;
           exact      -> jd_span null AND the hint is word for word inside one of
                         the criterion's requirement spans;
           equivalent -> jd_span required (all mapping guards above apply);
           none       -> jd_span null; if the hint is nevertheless word for word
                         inside a requirement span (embedded in a longer
                         qualified phrase) the criterion must report
                         ambiguous_relevance.
  V-anchor every requirement span contains an anchor (the selected duration, a
         verbatim hint match, a mapped jd_span or a JD-selected target) or is the
         line immediately after an anchored requirement span (wrapped bullet).
         Restriction anchor (s1-5): ONLY when none of the criterion's spans has
         an evidence anchor, a role/function/sector restriction span anchors its
         line (hint-less criteria always have years, so no duration ->
         n_not_in_jd: never resolves on its own).
         Statement anchor: ONLY when none of the criterion's spans contains any
         of those anchors, ONE span the model marks "experience_requirement":
         true anchors itself (e.g. "Experience in nursing is preferred.").
         Such a criterion can never resolve on its own: no anchor means no
         selected duration (n_not_in_jd when it has years) and no verbatim or
         mapped hint (target_not_in_jd for every hint); hint-less criteria
         always have years. Only explicit recruiter authority can resolve it.
         The marker is never an anchor next to other anchors, so it cannot be
         used to add About-us/context lines to an anchored requirement.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field

from services.s1_requirements.criteria import CriterionInput
from services.s1_requirements.durations import DurationMatch
from services.s1_requirements.jd_text import JDText, locate_words, normalize, words
from services.s1_requirements.schema import (
    AMB_AMBIGUOUS_RELEVANCE, AMB_REQUIREMENT_NOT_IN_JD, AMBIGUITY_CODES, BASIS_SECTOR, BASIS_TARGETS,
    BASIS_TOTAL_EXPERIENCE, BASIS_UNSPECIFIED, MATCH_EQUIVALENT, MATCH_EXACT, MATCH_NONE, MATCHES,
    POLICY_EXPLICIT_ROLE, POLICY_FUNCTIONAL, POLICY_MIXED, POLICY_PURE_DURATION, POLICY_SECTOR,
    RELEVANCE_BASES, TARGET_FUNCTION, TARGET_ROLE, TARGET_TYPES, Span,
    ALIGN_RELATIONS, EXTRA_MATERIAL, JD_EXTRA_KINDS, REL_ABBREVIATION, REL_FORM, REL_SAME, REL_TRANSLATION,
    RESTRICTION_FUNCTION, RESTRICTION_KINDS, RESTRICTION_ROLE, RESTRICTION_SECTOR, RESTRICTION_VAGUE,
)

MIN_SPAN_CHARS = 3
MAX_MAPPING_WORDS = 12

# error scopes (see services.s1_requirements.repair for how each is merged)
SCOPE_RESPONSE = "response"
SCOPE_CRITERION = "criterion"
SCOPE_SPANS = "requirement_spans"
SCOPE_SETTING = "setting"
SCOPE_DURATION = "duration"
SCOPE_AMBIGUITY = "ambiguity"
SCOPE_BASIS = "relevance_basis"
SCOPE_TARGETS_EXTRA = "targets_extra"            # non-hint targets added to a hint criterion
SCOPE_RESTRICTIONS = "restrictions"              # the restriction list as a whole (missing / not a list)
SCOPE_RESTRICTION_PREFIX = "restriction:"        # restriction:R0 (one restriction, by index)

ANCHOR_EVIDENCE = "evidence"         # duration / verbatim hint / mapped phrase
ANCHOR_RESTRICTION = "restriction"   # only a typed restriction span (criteria without analysis targets)
ANCHOR_STATEMENT = "statement"       # only the experience_requirement marker
SCOPE_TARGET_PREFIX = "target:"                  # target:T1 (whole hint target) | target:T1:<field> (one field of it)
                                                 # | target:J0 (hint-less target, by index)

POLICY_FROM_TYPES = "from_types"
POLICY_FROM_BASIS = "relevance_basis"


@dataclass(frozen=True)
class ParsedTarget:
    text: str
    type: str
    hint_id: str | None = None        # "T1".. when it is an analysis_json hint
    span: Span | None = None          # AI-selected JD span (hint-less target, or a hint's explicit mapping)
    match: str | None = None          # hint targets: exact | equivalent | none
    alignment: tuple = ()             # equivalent only: ({"hint", "jd", "relation"}, ...)
    jd_extra: tuple = ()              # equivalent only: ({"text", "kind"}, ...)


@dataclass(frozen=True)
class ParsedRestriction:
    text: str
    kind: str                         # role | function | sector | vague
    span: Span


@dataclass(frozen=True)
class ParsedCriterion:
    criterion_id: str
    policy: str                        # DERIVED (never the model's)
    requirement_spans: tuple[Span, ...]
    targets: tuple[ParsedTarget, ...]
    setting: Span | None
    duration_id: str | None
    ambiguity: tuple[str, ...]
    note: str = ""
    statement_anchored: bool = False   # anchored only by the model's experience_requirement marker
    relevance_basis: str | None = None
    policy_derivation: str = POLICY_FROM_TYPES
    model_policy: str | None = None    # diagnostics only; never controls the result
    restrictions: tuple = ()           # ParsedRestriction, criteria without analysis targets only
    anchor_kind: str = ANCHOR_EVIDENCE
    withdrawn: tuple = ()              # s1-5.1 audit records of equivalent claims withdrawn after repair


@dataclass(frozen=True)
class ScopedError:
    criterion_id: str | None
    scopes: tuple[str, ...]
    message: str


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    results: dict[str, ParsedCriterion] = field(default_factory=dict)
    scoped: list[ScopedError] = field(default_factory=list)


def hint_ids(c: CriterionInput) -> dict[str, str]:
    return {f"T{i}": t for i, t in enumerate(c.target_hints, 1)}


class _Errors:
    def __init__(self):
        self.messages: list[str] = []
        self.scoped: list[ScopedError] = []

    def add(self, cid: str | None, scopes, message: str) -> None:
        self.messages.append(message)
        self.scoped.append(ScopedError(cid, tuple(scopes), message))

    def __len__(self):
        return len(self.messages)


def _span(jd: JDText, obj, where: str, errs: list[str]) -> Span | None:
    if not isinstance(obj, dict) or not isinstance(obj.get("line"), int) or not isinstance(obj.get("text"), str):
        errs.append(f"{where}: must be an object {{\"line\": int, \"text\": str}}")
        return None
    text = obj["text"]
    if len(normalize(text)) < MIN_SPAN_CHARS:
        errs.append(f"{where}: text must be at least {MIN_SPAN_CHARS} characters")
        return None
    sp = jd.span_on_line(obj["line"], text, boundaries=True)
    if sp is None:
        errs.append(f"{where}: text {text!r} is not verbatim (whole words) on JD line {obj['line']}")
    return sp


def _inside(sp: Span, req: tuple[Span, ...]) -> bool:
    return any(sp.within(r) for r in req)


def _overlap(a: Span, b: Span) -> bool:
    return a.line == b.line and a.start < b.end and b.start < a.end


def tokens(s: str) -> list[str]:
    """Canonical words (jd_text.words): comparison form, a dotted acronym is one word."""
    return words(s)


def _proper_subphrase(small: list[str], big: list[str]) -> bool:
    """small is a contiguous, strictly shorter token run of big."""
    n = len(small)
    return 0 < n < len(big) and any(big[i:i + n] == small for i in range(len(big) - n + 1))


def _tscope(key: str, *fields: str) -> tuple[str, ...] | str:
    """target:<key> (whole target) or, with fields, (target:<key>:<field>, ...)."""
    if not fields:
        return SCOPE_TARGET_PREFIX + key
    return tuple(f"{SCOPE_TARGET_PREFIX}{key}:{f}" for f in fields)


def _mapping_errors(c: CriterionInput, targets: list[ParsedTarget], req: tuple[Span, ...], jd: JDText,
                    dur_span: Span | None, setting: Span | None, w: str) -> list[tuple[tuple[str, ...], str]]:
    errs: list[tuple[tuple[str, ...], str]] = []
    hints = hint_ids(c)
    verbatim = {hid: [s for s in jd.find(text) if any(s.within(r) for r in req)] for hid, text in hints.items()}
    mapped = [(t.hint_id, t.span) for t in targets if t.hint_id and t.span is not None]
    for hid, sp in mapped:
        tw = f"{w} target {hid} jd_span {sp.text!r}"
        sc = _tscope(hid, "jd_span", "alignment", "jd_extra")   # copy errors: the span (and its alignment) only
        sem = _tscope(hid, "jd_span", "match", "alignment", "jd_extra")   # not the same: may withdraw to none
        mt, ht = tokens(sp.text), tokens(hints[hid])
        if len(mt) > MAX_MAPPING_WORDS:
            errs.append((sc, f"{tw}: a mapping has at most {MAX_MAPPING_WORDS} words; map only the phrase naming "
                             f"the role/function"))
        if dur_span is not None and _overlap(sp, dur_span):
            errs.append((sc, f"{tw}: a mapping must not include the duration {dur_span.text!r}"))
        if setting is not None and _overlap(sp, setting):
            errs.append((sc, f"{tw}: a mapping must not include the setting {setting.text!r}"))
        if _proper_subphrase(mt, ht):
            errs.append((sem, f"{tw}: drops words of the target {hints[hid]!r}; that is not the same role/function: "
                              f"use null"))
        if _proper_subphrase(ht, mt):
            errs.append((sem + (SCOPE_AMBIGUITY,),
                         f"{tw}: adds words to the target {hints[hid]!r}; use null and, if the target appears "
                         f"only inside this longer phrase, report ambiguous_relevance"))
        for other, osp in mapped:
            if other != hid and hid < other and _overlap(sp, osp):
                errs.append((_tscope(hid, "jd_span", "match", "alignment", "jd_extra")
                             + _tscope(other, "jd_span", "match", "alignment", "jd_extra"),
                             f"{w}: hints {hid} and {other} are mapped to overlapping phrases; one phrase may be "
                             f"mapped by at most one hint: use null for both"))
        for other, spans in verbatim.items():
            if other != hid and any(_overlap(sp, v) for v in spans):
                errs.append((sem, f"{tw}: overlaps the verbatim text of hint {other} ({hints[other]!r})"))
    return errs


def implied_policy(types: list[str]) -> str | None:
    kinds = set(types)
    if not kinds:
        return None
    if kinds == {TARGET_ROLE}:
        return POLICY_EXPLICIT_ROLE
    if kinds == {TARGET_FUNCTION}:
        return POLICY_FUNCTIONAL
    return POLICY_MIXED


def _match_errors(c: CriterionInput, targets: list[ParsedTarget], req: tuple[Span, ...], jd: JDText,
                  amb: list[str], w: str, bad_span: set[str] = frozenset()) -> list[tuple[tuple[str, ...], str]]:
    """``bad_span``: hints whose supplied jd_span already failed span validation; that span error alone
    scopes the target (only the span may be repaired), so no match error is added for it."""
    errs: list[tuple[tuple[str, ...], str]] = []
    hints = hint_ids(c)
    for t in targets:
        if not t.hint_id or t.hint_id in bad_span:
            continue
        tw = f"{w} target {t.hint_id}"
        sc = _tscope(t.hint_id, "match", "jd_span", "alignment", "jd_extra")
        verbatim = any(_inside(s, req) for s in jd.find(hints[t.hint_id]))
        if t.match not in MATCHES:
            errs.append((sc, f"{tw}: match must be one of {list(MATCHES)}, got {t.match!r}"))
        elif t.match == MATCH_EXACT:
            if t.span is not None:
                errs.append((sc, f"{tw}: match exact takes jd_span null"))
            if not verbatim:
                errs.append((sc, f"{tw}: match exact but {hints[t.hint_id]!r} is not word for word inside this "
                                 f"criterion's requirement_spans; use equivalent (with jd_span) or none"))
        elif t.match == MATCH_EQUIVALENT:
            if t.span is None:
                errs.append((sc, f"{tw}: match equivalent requires jd_span (the verbatim JD phrase); if you are not "
                                 f"certain the meaning is the same, use none"))
        elif t.match == MATCH_NONE:
            if t.span is not None:
                errs.append((sc, f"{tw}: match none takes jd_span null"))
            if verbatim and AMB_AMBIGUOUS_RELEVANCE not in amb:
                errs.append((_tscope(t.hint_id, "match") + (SCOPE_AMBIGUITY,),
                             f"{tw}: {hints[t.hint_id]!r} is word for word inside the requirement_spans: use match "
                             f"exact, or, if it is only part of a longer qualified phrase, keep none and report "
                             f"ambiguous_relevance"))
    return errs


def _script(token: str) -> str:
    if any("\u0600" <= ch <= "\u06ff" for ch in token):
        return "arabic"
    if any("a" <= ch <= "z" for ch in token):
        return "latin"
    return "other"


def _acronym_letters(original: str) -> str | None:
    """An all-capitals Latin acronym (dots allowed: "PM", "P.M.", "UX") -> its lower-case letters."""
    letters = (original or "").replace(".", "").strip()
    if 2 <= len(letters) <= 6 and letters.isascii() and letters.isalpha() and letters.isupper():
        return letters.lower()
    return None


def _expands(letters: str, words: list[str]) -> bool:
    """Structural acronym check (no similarity): 1..len(letters) words, the first word starts with the first
    letter, and the letters occur in order in the words ("ux" -> user experience, "pm" -> project manager)."""
    if not (1 <= len(words) <= len(letters)) or not words[0].startswith(letters[0]):
        return False
    rest = iter("".join(words))
    return all(ch in rest for ch in letters)


def _run_in(part: list[str], whole: list[str]) -> bool:
    n = len(part)
    return 0 < n <= len(whole) and any(whole[i:i + n] == part for i in range(len(whole) - n + 1))


def _same_msg(pw: str, h: str, j: str, cross: bool) -> str:
    if cross:
        return (f"{pw}: {h!r} / {j!r} are in different languages; relation same is only for the identical word: "
                f"if they mean the same, use relation translation")
    return (f"{pw}: {h!r} / {j!r} are not the identical word; if the JD word is only another grammatical form "
            f"of the same word (plural, verb or noun form), use relation form; otherwise use match none")


def _alignment_errors(hid: str, hint_text: str, t: dict, sp: Span, w: str
                      ) -> tuple[list[tuple[tuple[str, ...], str]], tuple, tuple]:
    """s1-5 V-align: structural coverage of an "equivalent" mapping's word alignment.

    Every word of the analysis target is paired exactly once with verbatim words of the jd_span; every word of
    the jd_span is paired or listed in jd_extra exactly once; a "material" jd_extra word means the JD phrase
    adds meaning (never equivalent). One target word per pair, except a verified abbreviation; same-language
    pairs are one word to one word, so a qualifier cannot hide inside another word's pair. Whether each pair
    really means the same is the model's (audited) judgment.
    s1-5.1: words are the canonical jd_text words (a dotted acronym is one word) and a JD piece is located
    with the same boundary rule as spans (locate_words: an Arabic proclitic chain may stay in front of the
    first word, nothing else). Coverage is by word position in the jd_span."""
    errs: list[tuple[tuple[str, ...], str]] = []
    fmt = _tscope(hid, "alignment", "jd_extra")                       # copy/format: only the alignment
    sem = _tscope(hid, "alignment", "jd_extra", "match", "jd_span")   # coverage: may withdraw to none
    tw = f"{w} target {hid} alignment"
    al, ex = t.get("alignment"), t.get("jd_extra", [])
    ex = [] if ex is None else ex
    if not isinstance(al, list) or not al:
        return [(fmt, f"{tw}: match equivalent requires alignment: one pair per word of the target "
                      f"{hint_text!r}, e.g. {{\"hint\": \"<target word>\", \"jd\": \"<JD word(s)>\", "
                      f"\"relation\": \"same|form|translation|abbreviation\"}}")], (), ()
    if not isinstance(ex, list):
        return [(fmt, f"{tw}: jd_extra must be a list")], (), ()
    hint_toks, jd_toks = tokens(hint_text), tokens(sp.text)
    used_h: Counter = Counter()
    used_j: Counter = Counter()                       # jd_span word position -> times accounted for
    pairs, extras, abbr_letters = [], [], []

    def place(piece: list[str]) -> int | None:
        """Start position of ``piece`` in the jd_span, preferring words not yet accounted for."""
        found = [i for i, _ in locate_words(piece, jd_toks)]
        if not found:
            return None
        free = [i for i in found if all(used_j[i + k] == 0 for k in range(len(piece)))]
        return (free or found)[0]

    for k, p in enumerate(al):
        pw = f"{tw}[{k}]"
        if not (isinstance(p, dict) and isinstance(p.get("hint"), str) and isinstance(p.get("jd"), str)
                and p.get("relation") in ALIGN_RELATIONS):
            errs.append((fmt, f"{pw}: must be {{\"hint\": str, \"jd\": str, \"relation\": one of "
                              f"{list(ALIGN_RELATIONS)}}}"))
            continue
        ht, jt, rel = tokens(p["hint"]), tokens(p["jd"]), p["relation"]
        if not _run_in(ht, hint_toks):
            errs.append((fmt, f"{pw}: {p['hint']!r} is not word(s) of the target {hint_text!r}"))
            continue
        at = place(jt)
        if at is None:
            errs.append((fmt, f"{pw}: {p['jd']!r} is not whole word(s) of the jd_span {sp.text!r} (only the "
                              f"attached Arabic letters ك ب ل و ف may be left off the front of a word)"))
            continue
        used_h.update(ht)
        used_j.update(range(at, at + len(jt)))
        pairs.append({"hint": p["hint"], "jd": p["jd"], "relation": rel})
        hs, js = {_script(x) for x in ht}, {_script(x) for x in jt}
        cross = not (hs & js)
        if rel == REL_ABBREVIATION:
            hl = _acronym_letters(p["hint"]) if len(ht) == 1 and p["hint"] in hint_text else None
            jl = _acronym_letters(p["jd"]) if p["jd"] in sp.text and _acronym_letters(p["jd"]) else None
            if not ((hl and _expands(hl, jt)) or (jl and _expands(jl, ht))):
                errs.append((sem, f"{pw}: abbreviation needs an all-capitals acronym on one side whose letters "
                                  f"start and run through the words on the other side ({p['hint']!r} / {p['jd']!r})"))
            else:
                abbr_letters.append(hl or jl)
            continue
        if len(ht) != 1:
            errs.append((sem, f"{pw}: a pair maps ONE target word (got {p['hint']!r}); pair every target word "
                              f"separately"))
        elif rel == REL_SAME and not (len(ht) == len(jt) and locate_words(ht, jt)):   # same canonical word(s)
            errs.append((sem, _same_msg(pw, p["hint"], p["jd"], cross)))
        elif rel == REL_FORM and cross:
            errs.append((sem, f"{pw}: {p['hint']!r} / {p['jd']!r} are in different languages; relation form is "
                              f"only for the same language: if they mean the same, use relation translation"))
        elif rel == REL_FORM and len(jt) != 1:
            errs.append((sem, f"{pw}: relation form is one word to one word in the same language "
                              f"({p['hint']!r} / {p['jd']!r})"))
        elif rel == REL_TRANSLATION and not cross:
            errs.append((sem, f"{pw}: relation translation needs another language ({p['hint']!r} / {p['jd']!r}); "
                              f"in the same language pair one word to one word (same or form)"))
    for k, e in enumerate(ex):
        ew = f"{w} target {hid} jd_extra[{k}]"
        if not (isinstance(e, dict) and isinstance(e.get("text"), str) and e.get("kind") in JD_EXTRA_KINDS):
            errs.append((fmt, f"{ew}: must be {{\"text\": one JD word, \"kind\": \"grammatical\" | "
                              f"\"material\"}}"))
            continue
        et = tokens(e["text"])
        at = place(et) if len(et) == 1 else None
        if at is None:
            errs.append((fmt, f"{ew}: {e['text']!r} must be exactly one word of the jd_span {sp.text!r}"))
            continue
        used_j[at] += 1
        extras.append({"text": e["text"], "kind": e["kind"]})
        if e["kind"] == EXTRA_MATERIAL:
            errs.append((sem, f"{ew}: the JD phrase adds the material word {e['text']!r} that the target "
                              f"{hint_text!r} does not have: that is not the same role/function; use match none"))
    if not errs:
        missing = list((Counter(hint_toks) - used_h).elements())
        twice = list((used_h - Counter(hint_toks)).elements())
        if missing or twice:
            errs.append((sem, f"{tw}: every word of the target must be paired exactly once; unpaired {missing}, "
                              f"paired more than once {twice}. A target word with no JD counterpart means the JD "
                              f"phrase drops it: use match none"))
        unacc = [jd_toks[i] for i in range(len(jd_toks)) if used_j[i] == 0]
        over = [jd_toks[i] for i in range(len(jd_toks)) if used_j[i] > 1]
        if unacc and not over and any(_names_role_twice(a, unacc) for a in abbr_letters):
            errs.append((sem, f"{tw}: the jd_span names the same role twice; narrow jd_span to either the full form or "
                         f"the acronym (unaccounted {unacc})"))
        elif unacc or over:
            errs.append((sem, f"{tw}: every word of the jd_span must be paired or listed in jd_extra exactly once; "
                              f"unaccounted {unacc}, used more than once {over}"))
    return errs, tuple(pairs), tuple(extras)


def _names_role_twice(letters: str, unaccounted: list[str]) -> bool:
    """The unaccounted jd_span words are exactly the other form of an abbreviation already paired: the acronym
    itself ("pm") or words whose initials are exactly its letters ("project manager"). Structural, no
    dictionary; anything else stays an ordinary unaccounted-word error."""
    return unaccounted == [letters] or "".join(x[0] for x in unaccounted) == letters


def _anchor_errors(req: tuple[Span, ...], anchors: list[Span], marked: list[Span],
                   w: str, restriction_anchors: list[Span] = ()) -> tuple[list[str], str]:
    """Evidence anchors first. Only when NO span has one: typed restriction spans (criteria without analysis
    targets), then the experience_requirement marker. Neither can add a context line next to an anchored
    requirement, and neither can resolve on its own (no duration -> n_not_in_jd / no target evidence)."""
    anchored_lines = {r.line for r in req if any(a.within(r) for a in anchors)}
    errs: list[str] = []
    kind = ANCHOR_EVIDENCE
    if req and not anchored_lines and restriction_anchors:
        anchored_lines = {r.line for r in req if any(a.within(r) for a in restriction_anchors)}
        if anchored_lines:
            kind = ANCHOR_RESTRICTION
    if req and not anchored_lines and marked:
        if len(marked) > 1:
            return [f"{w}: at most one requirement span may be marked experience_requirement"], ANCHOR_EVIDENCE
        anchored_lines, kind = {marked[0].line}, ANCHOR_STATEMENT
    for r in req:
        if r.line in anchored_lines or (r.line - 1) in anchored_lines:
            continue
        errs.append(f"{w} requirement span {r.text!r} (line {r.line}) contains no anchor (the selected duration, "
                    f"a target or its mapped phrase) and does not continue an anchored line. Choose one: (1) quote "
                    f"a span of this criterion's requirement statement that contains such an anchor; (2) if this "
                    f"span itself is the genuine experience requirement (even though a target is match none and "
                    f"no duration is given), keep it and mark it \"experience_requirement\": true; (3) only if "
                    f"the JD contains no such experience requirement at all, return [] with requirement_not_in_jd")
    return errs, kind


def validate_response(raw: str, jd: JDText, criteria: list[CriterionInput],
                      durations: dict[str, tuple[int, DurationMatch]]) -> ValidationResult:
    E = _Errors()
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        E.add(None, (SCOPE_RESPONSE,), f"response is not valid JSON: {exc}")
        return ValidationResult(False, E.messages, scoped=E.scoped)
    items = data.get("criteria") if isinstance(data, dict) else None
    if not isinstance(items, list):
        E.add(None, (SCOPE_RESPONSE,), 'response must be an object with a "criteria" list')
        return ValidationResult(False, E.messages, scoped=E.scoped)

    by_id = {c.criterion_id: c for c in criteria}
    seen: dict[str, dict] = {}
    for i, it in enumerate(items):
        cid = it.get("criterion_id") if isinstance(it, dict) else None
        if cid not in by_id:
            E.add(None, (SCOPE_RESPONSE,), f"criteria[{i}]: unknown criterion_id {cid!r}")
        elif cid in seen:
            E.add(cid, (SCOPE_CRITERION,), f"criterion {cid}: returned more than once")
        else:
            seen[cid] = it
    for cid in by_id:
        if cid not in seen:
            E.add(cid, (SCOPE_CRITERION,), f"criterion {cid}: missing result")

    results: dict[str, ParsedCriterion] = {}
    for cid, it in seen.items():
        c = by_id[cid]
        w = f"criterion {cid}"
        n_err = len(E)

        def add(scopes, msg, _cid=cid):
            E.add(_cid, scopes, msg)

        def span(obj, where, scopes):
            msgs: list[str] = []
            sp = _span(jd, obj, where, msgs)
            for m in msgs:
                add(scopes, m)
            return sp

        model_policy = it.get("policy") if isinstance(it.get("policy"), str) else None
        amb = it.get("ambiguity") or []
        if not isinstance(amb, list) or any(a not in AMBIGUITY_CODES for a in amb) or len(set(amb)) != len(amb):
            add((SCOPE_AMBIGUITY,), f"{w}: ambiguity must be a list of distinct codes from {list(AMBIGUITY_CODES)}")
            amb = []

        # R3 requirement spans
        req: list[Span] = []
        rs = it.get("requirement_spans")
        if not isinstance(rs, list):
            add((SCOPE_SPANS,), f"{w}: requirement_spans must be a list")
            rs = []
        marked: list[Span] = []
        for j, obj in enumerate(rs):
            sp = span(obj, f"{w} requirement_spans[{j}]", (SCOPE_SPANS,))
            flag = obj.get("experience_requirement", False) if isinstance(obj, dict) else False
            if not isinstance(flag, bool):
                add((SCOPE_SPANS,), f"{w} requirement_spans[{j}]: experience_requirement must be true or false")
                flag = False
            if sp is not None:
                req.append(sp)
                if flag:
                    marked.append(sp)
        if not rs and AMB_REQUIREMENT_NOT_IN_JD not in amb:
            add((SCOPE_SPANS, SCOPE_AMBIGUITY), f"{w}: requirement_spans is empty; quote the JD requirement or "
                                                f"report {AMB_REQUIREMENT_NOT_IN_JD}")
        if rs and AMB_REQUIREMENT_NOT_IN_JD in amb:
            add((SCOPE_SPANS, SCOPE_AMBIGUITY), f"{w}: {AMB_REQUIREMENT_NOT_IN_JD} reported but requirement_spans "
                                                f"given")
        req_t = tuple(req)

        # R4 / R5 targets
        hints = hint_ids(c)
        targets: list[ParsedTarget] = []
        tl = it.get("targets")
        if not isinstance(tl, list):
            if hints or tl is not None:
                add(tuple(_tscope(h) for h in hints) or (SCOPE_TARGETS_EXTRA,), f"{w}: targets must be a list")
            tl = []
        used_hints: list[str] = []
        raw_targets: dict[str, dict] = {}
        bad_span: set[str] = set()
        for j, t in enumerate(tl):
            tw = f"{w} targets[{j}]"
            hid = t.get("hint") if isinstance(t, dict) else None
            tsc = (_tscope(hid),) if hid in hints else (_tscope(f"J{j}"),)
            if not isinstance(t, dict):
                add(tsc, f"{tw}: must be an object")
                continue
            ttype = t.get("type")
            if ttype not in TARGET_TYPES:
                add(_tscope(hid, "type") if hid in hints else tsc,
                    f"{tw}: type must be one of {list(TARGET_TYPES)}, got {ttype!r}")
            if "hint" in t:
                if hid not in hints:                      # treated like an added target: hint targets are kept
                    add((SCOPE_TARGETS_EXTRA,) if hints else (_tscope(f"J{j}"),),
                        f"{tw}: unknown hint {hid!r} (valid: {sorted(hints)})")
                    continue
                if "text" in t and t["text"] != hints[hid]:
                    add(_tscope(hid, "text"), f"{tw}: hint {hid} text must be returned unchanged as {hints[hid]!r}")
                used_hints.append(hid)
                mapped = None
                if t.get("jd_span") is not None:          # explicit mapping to the JD requirement
                    jsc = _tscope(hid, "jd_span", "alignment", "jd_extra")   # span copy error: never match/type
                    mapped = span(t["jd_span"], f"{tw} jd_span", jsc)
                    if mapped is not None and not _inside(mapped, req_t):
                        add(jsc, f"{tw} jd_span: {mapped.text!r} is not inside one of this "
                                 f"criterion's requirement_spans")
                        mapped = None
                    if mapped is None:
                        bad_span.add(hid)
                if ttype in TARGET_TYPES:
                    targets.append(ParsedTarget(hints[hid], ttype, hint_id=hid, span=mapped, match=t.get("match")))
                    raw_targets[hid] = t
            else:
                if hints:
                    add((SCOPE_TARGETS_EXTRA,),
                        f"{tw}: this criterion has target hints {sorted(hints)}; return exactly those and add no "
                        f"other targets")
                else:
                    add((SCOPE_TARGETS_EXTRA,),
                        f"{tw}: criteria without target_hints return restrictions, not targets; targets are "
                        f"derived from role/function restrictions")
                continue
        for hid in sorted(hints):
            k = used_hints.count(hid)
            if k != 1:
                add((_tscope(hid),), f"{w}: hint {hid} ({hints[hid]!r}) must appear exactly once in targets, "
                                     f"found {k}: keep ONE object for {hid} with one type, one match and, if "
                                     f"equivalent, one complete alignment")

        # R6 setting
        setting = None
        so = it.get("setting")
        if so is not None:
            setting = span(so, f"{w} setting", (SCOPE_SETTING,))
            if setting is not None and not _inside(setting, req_t):
                add((SCOPE_SETTING, SCOPE_SPANS), f"{w} setting: {setting.text!r} is not inside one of this "
                                                  f"criterion's requirement_spans")
                setting = None

        # R7 duration
        dur = it.get("duration")
        if dur is not None:
            if dur not in durations:
                add((SCOPE_DURATION,), f"{w}: duration must be null or one of {sorted(durations)}, got {dur!r}")
                dur = None
            elif not c.has_years:
                add((SCOPE_DURATION,), f"{w}: this criterion has no years requirement; duration must be null")
                dur = None
            else:
                line, m = durations[dur]
                if not _inside(Span(line, m.start, m.end, m.text), req_t):
                    add((SCOPE_DURATION, SCOPE_SPANS),
                        f"{w}: duration {dur} ({m.text!r}, line {line}) is not inside one of this criterion's "
                        f"requirement_spans")
                    dur = None

        # s1-5 typed restrictions (criteria WITHOUT analysis targets) -> targets / setting / basis (derived)
        restrictions: list[ParsedRestriction] = []
        r_index: list[int] = []                    # position of each parsed restriction in the raw list
        rl = it.get("restrictions")
        if hints:
            if rl not in (None, []):
                add((SCOPE_RESTRICTIONS,), f"{w}: restrictions apply only to criteria without target_hints; omit them")
        elif not isinstance(rl, list):
            add((SCOPE_RESTRICTIONS,), f"{w}: restrictions must be a list of every phrase that limits which experience "
                                       f"counts ([] only for general experience with no restriction at all)")
        else:
            seen_r: set[tuple[str, str]] = set()
            for j, r in enumerate(rl):
                rsc = (f"{SCOPE_RESTRICTION_PREFIX}R{j}",)
                rw = f"{w} restrictions[{j}]"
                if not isinstance(r, dict) or r.get("kind") not in RESTRICTION_KINDS:
                    add(rsc, f"{rw}: must be {{\"line\", \"text\", \"kind\": one of {list(RESTRICTION_KINDS)}}}")
                    continue
                rsp = span(r, rw, rsc)
                if rsp is None:
                    continue
                if not _inside(rsp, req_t):
                    add(rsc, f"{rw}: {rsp.text!r} is not inside one of this criterion's requirement_spans")
                    continue
                key = (normalize(rsp.text), r["kind"])
                if key in seen_r:
                    add(rsc, f"{rw}: duplicate restriction {rsp.text!r}")
                    continue
                seen_r.add(key)
                restrictions.append(ParsedRestriction(rsp.text, r["kind"], rsp))
                r_index.append(j)
            if so is not None:
                add((SCOPE_SETTING,), f"{w}: for criteria without target_hints the setting comes from a sector "
                                      f"restriction; return setting null")
                setting = None
        rf = [r for r in restrictions if r.kind in (RESTRICTION_ROLE, RESTRICTION_FUNCTION)]
        sec = [r for r in restrictions if r.kind == RESTRICTION_SECTOR]
        vag = [r for r in restrictions if r.kind == RESTRICTION_VAGUE]
        if not hints and len(sec) > 1:
            add((SCOPE_RESTRICTIONS,) + tuple(f"{SCOPE_RESTRICTION_PREFIX}R{j}" for j in range(len(rl or []))),
                f"{w}: give one sector restriction per requirement (got {[r.text for r in sec]})")
        if not hints:
            targets = [ParsedTarget(r.text, r.kind, span=r.span) for r in rf]
            if len(sec) == 1:
                setting = sec[0].span

        # policy derivation (never the model's policy; s1-5: never the model's relevance basis either)
        types = [t.type for t in targets]
        policy, derivation = implied_policy(types), POLICY_FROM_TYPES
        basis = None
        if not hints:
            if rf:
                basis = BASIS_TARGETS
            elif sec:
                basis, policy, derivation = BASIS_SECTOR, POLICY_SECTOR, POLICY_FROM_BASIS
            elif vag:
                basis, policy, derivation = BASIS_UNSPECIFIED, POLICY_PURE_DURATION, POLICY_FROM_BASIS
            else:
                basis, policy, derivation = BASIS_TOTAL_EXPERIENCE, POLICY_PURE_DURATION, POLICY_FROM_BASIS
            if policy == POLICY_PURE_DURATION and not c.has_years:
                add((SCOPE_RESTRICTIONS,), f"{w}: a requirement without role/function/sector restrictions needs a "
                                           f"years requirement")

        # s1-2 mapping guards, s1-3 match rules and requirement-span anchoring
        dur_span = None
        if dur is not None:
            dl, dm = durations[dur]
            dur_span = Span(dl, dm.start, dm.end, dm.text)
        for scopes, msg in _mapping_errors(c, targets, req_t, jd, dur_span, setting, w):
            add(scopes, msg)
        for scopes, msg in _match_errors(c, targets, req_t, jd, list(amb), w, bad_span):
            add(scopes, msg)
        aligned: dict[str, tuple] = {}
        for t in targets:
            if t.hint_id and t.match == MATCH_EQUIVALENT and t.span is not None:
                a_errs, pairs, extras = _alignment_errors(t.hint_id, t.text, raw_targets[t.hint_id], t.span, w)
                for scopes, msg in a_errs:
                    add(scopes, msg)
                aligned[t.hint_id] = (pairs, extras)
        if aligned:
            targets = [ParsedTarget(t.text, t.type, t.hint_id, t.span, t.match, *aligned[t.hint_id])
                       if t.hint_id in aligned else t for t in targets]
        if dur_span is not None:
            for j, r in zip(r_index, restrictions):
                if _overlap(r.span, dur_span):
                    add((f"{SCOPE_RESTRICTION_PREFIX}R{j}",), f"{w} restrictions[{j}]: {r.text!r} must not include "
                                                               f"the duration {dur_span.text!r}")
        anchors = [s for s in (dur_span,) if s is not None]
        anchors += [t.span for t in targets if t.span is not None and t.hint_id]
        anchors += [s for text in hints.values() for s in jd.find(text) if _inside(s, req_t)]
        r_anchors = [r.span for r in restrictions if r.kind != RESTRICTION_VAGUE]
        anchor_errs, anchor_kind = _anchor_errors(req_t, anchors, marked, w, r_anchors)
        statement_anchored = anchor_kind != ANCHOR_EVIDENCE
        for msg in anchor_errs:
            add((SCOPE_SPANS,), msg)

        note = it.get("note") if isinstance(it.get("note"), str) else ""
        if len(E) == n_err and policy is not None:
            results[cid] = ParsedCriterion(cid, policy, req_t, tuple(targets), setting, dur, tuple(amb), note,
                                           statement_anchored, basis, derivation, model_policy,
                                           tuple(restrictions), anchor_kind)
        elif len(E) == n_err:                                # defensive: no derivable policy
            add((SCOPE_CRITERION,), f"{w}: no policy can be derived (no valid targets and no relevance_basis)")

    ok = not E.messages
    return ValidationResult(ok, E.messages, results if ok else {}, E.scoped)
