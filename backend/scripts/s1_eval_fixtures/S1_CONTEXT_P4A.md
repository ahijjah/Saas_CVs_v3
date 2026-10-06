# S1 s1-6.0 — experience contexts (Architecture C P4a)

Status: **implemented offline; no real model evaluation has been run.** S1 and S2 stay shadow-only; the
qualifying-context (QC) feature flag stays OFF; candidate_qc-1, its prompt and the QC fixtures are unchanged.

## Versions

| item | before | P4a |
|---|---|---|
| `S1_PROMPT_VERSION` | s1-5.2 (fingerprint `4f22dddb117e`) | **s1-6.0** (fingerprint `af9f496563a4`) |
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
  restricts only some alternatives, only part of the experience (compound), or is softened. Settings stay `[]`;
  `ambiguous_relevance` is no longer used for this.
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
| `s1_ctx_main_cases.json` (tuning) | s1-ctx-main-1 | 44 (45) | 35 / 9 | 28 / 13 / 4 | `c72803fbdddb45bd1c6ea45a4e8c949fdd9ea5742e20d0c511bdfd919954fc4b` |
| `s1_ctx_heldout_cases.json` (sealed) | s1-ctx-heldout-1 | 23 (24) | 18 / 5 | 14 / 7 / 3 | `bb949d061ddcb4a5d0d6a0d10b6af6794d4279a4e3f28d0d44fc561d55b94165` |

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

Tuning rules: tune on main only; never edit a fixture after seeing real output; run the held-out at most once per
prompt version — **once used it is permanently exposed** and can never again be unseen validation; a held-out
failure needs a new prompt version AND a new held-out set.
