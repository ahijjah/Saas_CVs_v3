# S1 s1-6.0 — experience contexts (Architecture C P4a)

Status: **implemented offline; no real model evaluation has been run.** S1 and S2 stay shadow-only; the
qualifying-context (QC) feature flag stays OFF; candidate_qc-1, its prompt and the QC fixtures are unchanged.

## Versions

| item | before | P4a |
|---|---|---|
| `S1_PROMPT_VERSION` | s1-5.2 (fingerprint `4f22dddb117e`) | **s1-6.0** (fingerprint `5b4172f709b2`; `af9f496563a4` before the pre-evaluation correction below) |
| `S1_VERSION` | 1.4.5 | **1.5.0** |
| `S1_SCHEMA` | s1_requirement_spec_v2 | **s1_requirement_spec_v3** (a v2 object is never read as v3) |
| `S1_INPUT_VERSION` | s1-in-1 | s1-in-1 (unchanged: the model input carries no qualifying context) |
| cache key | `sha256(s1|S1_VERSION|input_hash|PROMPT_VERSION:fingerprint|model)` | unchanged formula; every s1-5.2 entry is unreachable (version + fingerprint) |

Old s1-5.2 real results are **not** validation evidence for s1-6.0.

## Semantics

- `setting: Setting | None` became `settings: tuple[Setting, ...]`: 0–5 experience **contexts** (where / in what
  setting otherwise relevant past experience must have been gained: geographic scope, organisation type, sector
  or domain, project type, work setting). Each is a verbatim JD span inside the criterion's requirement spans,
  distinct, non-overlapping, ordered by JD occurrence. Several entries = AND on the SAME experience entry.
  One contiguous restriction is ONE entry; an "or" stays inside one entry. Nothing is joined or split.
- Prompt: the categorical exclusions of "a location" and "multinational" are gone. Excluded instead: employer
  name / About-us, the vacancy's location, duties of the new job, seniority, tools, generic adjectives /
  culture / environment, and "multinational / international / global" when describing the hiring employer or
  team. A separate sentence restricting the experience ("All of this experience must have been gained in …")
  is part of the requirement spans. Preferred wording follows candidate_qc-1: a whole preferred requirement keeps
  its context; a softened qualifier ("preferably / ideally in X") is ambiguous scope.
- New ambiguity reason **`ambiguous_context_scope`** (kind ambiguity → needs_confirmation): a context exists but
  it is unclear which target / alternative it applies to (it restricts only some alternatives), or it is softened.
  Settings stay `[]`; `ambiguous_relevance` is no longer used for this.
- **`compound_requirement` is a different reason** and the two are never coupled: a nested sub-duration
  ("N years overall, including M years in X") is a requirement STRUCTURE the model cannot represent; the context
  itself is not ambiguous. Result: settings `[]`, `compound_requirement` (deterministic, from the duration
  parser), and NO `ambiguous_context_scope` unless a genuine alternative-scope ambiguity also exists.
- Hint-less criteria: restriction wire kind `sector` was renamed **`context`** (a geographic or organisation-type
  restriction labelled "sector" invites the model to drop it). Several context restrictions are allowed (the old
  "one sector restriction" contract error is gone); contexts only → policy `sector` (basis "sector" now means
  context-only relevance).
- `context_resolution` (schema slot only): `{status resolved|unconfirmed, detail, effective {state, contexts,
  provenance recruiter_edited|recruiter_confirmed|jd_verified} | null, record}` with invariants (resolved ⇔
  effective). S1 never creates one; the P4c resolver/comparator is **not** implemented.

## Validator / repair

- Settings list: ≤ 5, verbatim (whole words, Arabic proclitics), inside the requirement spans, distinct,
  pairwise non-overlapping, not overlapping the selected duration, not overlapping a verbatim target (a context
  is never part of a target); JD order. A legacy non-null `setting` key is an **error** (never silently ignored);
  a missing `settings` key means no context, as a missing v2 `setting` meant null.
- Context anchor: a requirement span holding a validated context anchors its line only if it comes AFTER an
  already anchored line of the criterion, at most 3 lines later; it never anchors alone and never continues
  onto the next line. Text before the requirement (About-us, headers) can never be attached.
- Repair (`contexts_preserved`): the repair's contexts replace the main ones only if every error-free main
  context is kept (same line + text) or lies inside one contiguous repair context, and every other main context
  is replaced one-for-one. A main restriction of unknown kind must reappear with its text. The only exception:
  the merged answer reports `ambiguous_context_scope` (then the criterion cannot resolve). Material lock, scoped
  merge, pair merge, normalisation and equivalence withdrawal are unchanged.

## Fail-closed S2 views (`assemble.s2_views`), independent of `require_resolved`

| condition | result |
|---|---|
| compound requirement / unverified equivalence | `S1ViewError` (unchanged, checked first) |
| no `context_resolution` | `S1ViewError` code `context_unresolved` |
| `context_resolution.status != resolved` | `S1ViewError` code `context_unconfirmed` |
| more than one effective context | `S1ViewError` code `multi_context_unsupported` |
| `ambiguous_context_scope` without a recruiter resolution | `S1ViewError` code `context_scope_ambiguous` |
| sector policy without exactly one effective context; pure-duration with a context | `S1ViewError` |

The view's setting comes only from `context_resolution.effective`, never from `art.settings`; the spec version
covers the resolution. Since S1 never creates a resolution, **no S1 artefact has an S2 view in P4a** (shadow
only). There is no bypass parameter. `RequirementSpec` and S2 are unchanged.

## Independence (tests/test_s1_context_p4a.py::TestIndependence)

For 19 variants of `analysis_json` (qualifying_context absent / identified / none / uncertain / analysis /
recruiter / different contexts / malformed; qualifying_context_audit current / recruiter / latest_run /
failed run / malformed, alone and combined) the following are identical to the base: enumeration, criterion
ids, user message bytes, system prompt, input hash, cache key, main and repair call messages, call count,
cache-hit behaviour (one cache entry serves all variants) and artefact semantics. Controls: changing
`minimum_years` or `relevant_roles` does change them. Static AST guards: `classifier`, `criteria`,
`validator`, `repair`, `jd_text`, `durations` import nothing QC / agreement related and contain no such names
or string literals; entry-point signatures take no QC parameter. A mutation check proves the tests detect a leak.

## Existing tests re-baselined

- **setting → settings / wire kind sector → context** (`tests/test_s1_requirements.py`): the legacy-fill helper
  `with_match` gained a documented s1-6 wire adapter (a pre-s1-6 hint item's `setting` → `settings`; restriction
  kind `sector` → `context`; tests of the s1-6 contract write `settings` and are never adapted).
  `TestJob2026_0031::test_fixture_result`, `TestPolicies::test_sector`, `TestPolicies::test_mixed_groups_into_two_homogeneous_views`,
  `TestAuthority::test_setting_accepted_only_inside_requirement_span`, `TestAuthority::test_domain_knowledge_never_becomes_setting`,
  `TestS12ValidatorGuards::test_v_m8_no_duration_or_setting_in_mapping` (message "setting" → "context"),
  `TestS12ValidatorGuards::test_v_anchor_wrapped_continuation_line_is_valid`, `TestStatementAnchor::test_sector_without_duration`,
  `TestStatementAnchor::test_assembler_invariant`, `TestS151Withdrawal::test_assembler_refuses_a_withdrawn_target_that_establishes_evidence`
  (ParsedCriterion settings tuple), `TestValidator::test_rejections` (`settings[0]: must be an object`),
  `TestS14PolicyDerivation::test_restriction_derivation` (kind `context`, settings list).
- **multiple contexts**: `TestS14PolicyDerivation::test_restriction_structure_is_strict` — the "one sector
  restriction" case is replaced by: retired kind `sector` rejected, overlapping contexts rejected, a context
  overlapping a role rejected, hint-less `settings` rejected, legacy `setting` rejected.
- **new ambiguity reason**: same test — `ambiguous_context_scope` together with a context restriction rejected.
- **changed prompt semantics / versions**: `TestS12Versioning::test_versions_and_fingerprint` (+ new
  `test_cache_identity_differs_from_s1_5_2`), `TestS12Versioning::test_prompt_contract` (s1-6 fragments; the
  location / "multinational" exclusions and the single setting must be gone), `TestS152FormNotTrustBearing::test_reason_taxonomy`
  (schema v3).
- **fail-closed views** (the S1 artefact alone has no view; view-shape tests attach an EXPLICIT, test-written
  recruiter resolution via `with_resolution`, never derived from `art.settings`; the helper `views()` first
  asserts `context_unresolved` for both `require_resolved` values): `test_fixture_result`,
  `test_view_criterion_text_still_masks_with_unchanged_s2_masking`, all `TestPolicies` view tests,
  `TestAuthority::test_target_absent_from_jd_needs_confirmation`, `TestTargetProvenance::test_valid_ai_mapping_is_jd_asserted`,
  `TestDeterminism::test_views_are_deterministic`, `TestS152FormNotTrustBearing::test_e_no_s2_view_even_for_a_permissive_caller`
  (an ordinary needs_confirmation artefact gets a preview only once its context is resolved),
  `TestS152FormNotTrustBearing::test_c_trust_bearing_equivalent_stays_jd_asserted`, `TestS1521PairMerge::test_m_translation_trust_unchanged`,
  `TestS1522Normalization::test_i_to_l_k1_normalized_without_a_repair_call`, `TestS15221PostRepairNormalization::test_k1_real_answers_recover_after_repair`,
  `TestS14PolicyDerivation::test_restriction_derivation` (unspecified basis). The view `spec_version` is the
  resolved artefact's (`test_fixture_result`, `test_mixed_groups_into_two_homogeneous_views`).
- **boundary harness** (`tests/test_s1_boundary_regression.py`): the fixture files are unchanged; `load_cases`
  adapts them in memory (`adapt_legacy_case`). Re-baselined: `TestFixture::test_labels_are_complete`,
  `TestSafety::test_offline_modes_never_create_a_real_client` and `test_s1_prompt_unchanged` (fingerprint),
  `fixture_phrases`, `_variants`, `_with_setting`, `TestTargetSettingFamily::test_composition`,
  `test_setting_variants_are_only_faithful_verbatim_spans`, `test_equivalent_variants_pass_missing_or_different_setting_fails`,
  `test_m1_variants` (kind `context`), `test_null_setting_controls_reject_an_invented_setting` (an invented context
  overlapping the target is now a validation failure). New: real mode needs `--confirm-real`; legacy fixture
  files unchanged and adapted in memory.

Offline oracle regression with the ported harness: **s1-boundary-7 45/45 pass, s1-heldout-6 21/21 pass**
(oracle answers only; no model).

## P4 context evaluation (scripts/s1_context_eval.py) — prepared, NOT run

| fixture | version | cases (criteria) | EN / AR | positive / negative / scope criteria | SHA256 |
|---|---|---|---|---|---|
| `s1_ctx_main_cases.json` (tuning) | s1-ctx-main-1 | 44 (45) | 35 / 9 | 28 / 13 / 3 (+1 compound) | `cf5844a22092a43d296e227de317ac75c6919f7d77e21da618c3945b4f0ca975` |
| `s1_ctx_heldout_cases.json` (sealed) | s1-ctx-heldout-1 | 23 (24) | 18 / 5 | 14 / 7 / 2 (+1 compound) | `fb917b610c0bd31c1932818655a3392469a827df74a02fc3344e7e288c78589c` |

Families: geographic, multinational positive / negative, government / public sector, banking / financial,
other sector, project type, organisation type, two AND contexts, OR inside one phrase, generic environment,
About-us, new-job duty, job location, preferred (whole vs softened), separate requirement sentence,
role-specific (two role-only criteria), alternative-specific (`ambiguous_context_scope`), Arabic prefixes,
contiguous vs split, compound (stays blocked), tools. The held-out vocabulary is disjoint from the main
fixture, both S1 fixtures, both QC fixtures and the prompt (leakage test). Both fixtures' oracle answers validate
and score 100% offline with every offline hard gate passing.

Real mode: `--confirm-real` required; held-out additionally `--allow-heldout`; pins on prompt version +
fingerprint, S1 version, model `gpt-4o-mini`, temperature 0.0, max tokens 4000 and both fixture SHA256 (any
mismatch refused); client `max_retries=0`, no fallback model; only S1's own single scoped repair may follow a
main call; every main / repair call recorded (model, temperature, raw content); technical and validation failures
recorded as such (settings `None`, never "no context"). Default 5 runs per case.

Gates (`evaluate_gates`):
- hard (= 0): setting outside requirement spans; ungrounded setting; failed artefact treated as "no context";
  S2 view for an unresolved context; independence-probe failure; false agreed / false agreed_none of the joint
  QC × S1 replay (reported `not_available` until P4c).
- main: settings-set accuracy ≥ .85, false-none ≤ .05, false-context ≤ .10; held-out: ≥ .80 / ≤ .05 / ≤ .10;
  ≥ 90% of cases with identical canonical settings in every run; technical + validation failures ≤ 5%;
  old-boundary non-context fields no worse than the s1-5.2 record (boundary harness, separately).
- informational: status / reason accuracy; joint QC/S1 agreement (soft .70, needs P4c).

### Pre-exposure correction (compound vs context ambiguity)

The first P4a commit (35d95b9) labelled the two compound cases as context ambiguity. Corrected before ANY model
had seen either fixture (no real run of S1 s1-6.0 has ever been made; main and held-out are both unexposed):

| case | before | after |
|---|---|---|
| CM43 (main) "8 years of overall experience as an Auditor, including at least 3 years in the GCC region" | oracle ambiguity `["ambiguous_context_scope"]`; expected reasons `[compound_requirement, ambiguous_context_scope]`; polarity `scope` | oracle ambiguity `[]`; expected reasons `[compound_requirement]`; polarity `compound` |
| CX22 (held-out) "10 years of overall experience as a Ship Captain, including at least 4 years in Arctic waters" | same as above | same as above |

Settings (`[]`) and status (`needs_confirmation`) are unchanged; no other fixture byte changed. SHA pins:
main `c72803fb…54fc4b` → `{M[:8]}…{M[-6:]}`, held-out `bb949d06…b94165` → `{H[:8]}…{H[-6:]}`. The prompt's
AMBIGUOUS SCOPE rule lost its compound example and gained a separate PART DURATION rule (settings `[]`, no
`ambiguous_context_scope`, the code records `compound_requirement`), so the s1-6.0 fingerprint moved
`af9f496563a4` → `5b4172f709b2` (the version label stays s1-6.0: it was never evaluated). Leakage and oracle
validation were rerun on both fixtures (100%, all offline hard gates pass). Tests:
`tests/test_s1_context_p4a.py::TestCompoundIsNotContextAmbiguity` (compound → `compound_requirement` only,
settings `[]`; alternative-specific context → `ambiguous_context_scope` only; the two reasons come from separate
sources and are never derived from each other; both fail closed in `s2_views`), the prompt contract in
`TestPromptS16::test_semantics`, and the fixture label rules in `tests/test_s1_context_eval.py`.

Tuning rules: tune on main only; never edit a fixture after seeing real output; run the held-out at most once per
prompt version — **once used it is permanently exposed** and can never again be unseen validation; a held-out
failure needs a new prompt version AND a new held-out set.

## P4b RC1 implementation fixes (prompt s1-6.0 and both fixtures unchanged)

Found by the real MAIN run (`p4b_main_recorded_regressions.json` holds 15 recorded main/repair outputs, replayed
offline by `tests/test_s1_p4b_rc1.py`):

1. **Hint-less repair merge** (`repair._merge_hintless`): a hint-less main answer's `settings` (invalid by
   contract) are never kept; each must reappear as a `context` restriction of the repair (`same_context`: same
   phrase, a longer phrase containing it, or the phrase minus leading in/on/within/at/the/a/an/في/ضمن/لدى/داخل);
   a restriction list supplied ONLY by the repair must name a role or function (never a context-only, vague or
   total-experience reading). Real effect on replay: 9 rejected-but-correct runs now pass (CM02, CM03, CM10,
   CM21); 18 unsafe target-dropping repairs now fail closed (CM09, CM15, CM30, CM02, CM05).
2. **Guidance** (`validator.CONTEXT_SPAN_GUIDANCE`): a context outside the requirement spans stays rejected;
   the message now tells the repair to add its JD sentence to `requirement_spans` (a context restriction's
   error now also opens `requirement_spans` to the repair, as a setting's already did). No span is added by code.
3. **Harness** (`s1_context_eval.target_check`, `--rescore`): role/function target loss and policy downgrade
   (gold derived from each criterion's oracle answer), reported separately, part of `pass`, and the hard gate
   `unsafe_target_policy_loss`. Re-scoring the real s1-6.0 MAIN records: 26 unsafe criterion-runs that had
   passed on context alone (CM02, CM05, CM09, CM15, CM21, CM30, CM38).

## S1 s1-6.1 (P4b prompt tuning, RC2 + RC3) — implemented offline, NOT yet evaluated

`S1_PROMPT_VERSION = s1-6.1`, `S1_VERSION = 1.5.1` (also covers the RC1 code), fingerprint `c3168587aeca`
(full SHA256 `c3168587aeca8238cf996d54630c2067c35b92e453d337a022aa4a627dde93ee`). Schema, validator, repair and
fixtures unchanged. Prompt changes:

- §3: the "never map to … sector, project or industry" wording is replaced by "a phrase saying where the work
  was done is never part of the target: it is a context".
- §4: every hint-less criterion MUST return `restrictions`; a context never replaces a role/function; an "or"
  alternative is its own role/function restriction.
- §5 restructured: STEP A decisions FIRST (A1 part duration → no context, no code; A2 one alternative → keep
  both alternatives, no context, `ambiguous_context_scope`; A3 softened → no context, code; whole-preferred
  contrast; A4 working environment incl. Arabic; A5 not about past experience), then STEP B (every other
  where/sector/organisation/project/region phrase is a context, also on the role's own line; title words vs
  context), then STEP C (shortest complete phrase, never a whole sentence; contiguous = one; separate = AND;
  "or" between places stays, "or" before a role/function is an alternative). "work setting" → "physical work
  site".
- Output examples: the first hint example carries a same-line context; a new example has
  `"ambiguity": ["ambiguous_context_scope"]` with two targets and settings `[]`.

Real MAIN acceptance (unchanged): hard = 0 (unsafe_target_policy_loss, outside spans, ungrounded, failed as
none, unresolved view, independence); settings ≥ .85, false-none ≤ .05, false-context ≤ .10, stability ≥ .90,
technical + validation failures ≤ .05. Family groups (alternative/scope, softened, compound, OR, generic
environment, same-line, AND, Arabic) are reported as `diagnostic_groups` only, never gates.
