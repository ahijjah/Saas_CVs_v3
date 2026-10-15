# Omission-coverage prototype (offline)

Scope: measures how much of a job description (JD) a stored extraction answer accounts for **by quotation**, and classifies every
uncovered span. It is an offline replay of stored answers. It is not production code, is not imported by the backend, and makes no model
or API calls. The frozen scorer `scripts/requirements_v2_tc20_eval.py` is reused read-only (`score_answer`, `_items`). Frozen artifacts and
reviewer judgments are not modified.

## Files
- `inventory.py`: source units from the ORIGINAL JD text, with stable ids `<sha12>:L<line>:<start>-<end>`.
- `coverage.py`: quotation coverage (union of credited spans, threshold, ambiguity, verbatim gate, exact uncovered spans).
- `annotations/tc20.json`: reviewed heading and background lines for the TC20 JD (exact text, keyed by the JD sha256).
- `replay.py`: reads the stored TC20 answers and writes `results/replay.json`. Stored files are not modified (tested).
- `preview.py`: writes `preview/inventory_guided_request.txt`: the pinned baseline request plus one marked addition. Not sent.
- `tests/`: 64 offline tests. Run `python -m pytest prototypes/omission_inventory/tests -q` from `backend/`.

## What the numbers mean (read this first)
- **Quoted** (`covered`) means credited quotes account for at least 0.9 of a unit's non-space characters. It does not mean complete,
  and it does not mean the meaning matches. `all text quoted` is the strict count: no uncovered text at all.
- **Uncovered spans** are exact original offsets. Each span gets exactly one class:
  - `punctuation_only`: no letters or digits (usually the final full stop). Not a clause.
  - `intro_wording`: at the unit's opening, at most 12 words, all lead-in words or words of the job title, and the rest of the unit is
    quoted. The answer dropped an opening phrase, not content.
  - `paraphrase`: a non-verbatim answer quote shares at least 0.6 of the span's content words. The answer restated the text, which the
    verbatim rule does not credit.
  - `genuine_omission`: none of the above. No verbatim quote and no paraphrase accounts for this text. This is a **review candidate**,
    not a proven semantic omission.
- Any unit with a word in its uncovered text sets `clause_review_required`. A unit can be covered and still need that review.
- OR/AND and routing are reported separately from coverage, using the frozen checks.

## Rules (fixed before the replay was run)
1. Units are physical lines. A soft wrap joins two lines only if they are adjacent (no blank line or whitespace-only line between),
   the second starts with a lowercase Latin letter, and the first has no terminal punctuation. Arabic is never joined (known limit).
2. Headings and background come only from a reviewed annotation keyed by the JD hash. Length and punctuation never classify a line.
3. A quote earns credit only if it is an exact substring of the original JD (the frozen verbatim check, unchanged).
4. A quote that occurs more than once in the JD is ambiguous and earns no credit. An uncovered unit that an ambiguous quote touches is
   routed to `ambiguous`, not `omitted`.
5. Covered at 0.9 or more, partial above 0 and below 0.9, omitted at 0. A short quote cannot cover a long unit.
6. Lists read: the seven category arrays, `non_scoreable_requirements`, `post_hiring_conditions`, `informational_items`.
7. No normalised offsets are used for any decision.

## Replay results (stored answers, no new calls)
Denominator: 35 requirement units of the TC20 JD (9 headings and 3 background lines excluded). "Spans" are uncovered spans.

**gpt-4.1 repeat (`tests/fixtures/requirements_v2_tc20_repeat`)**

| Arm, call | Quoted | All text quoted | Omitted units | Genuine spans | Paraphrase | Intro wording | Duty omissions (of 16) |
|---|---|---|---|---|---|---|---|
| v2-3, calls 1–4 | 34/35 | 34 | 0 | 0 | 0 | 1 each | 0 |
| v2-3, call 5 | 18/35 | 18 | 16 | 16 | 0 | 1 | **16** |
| v2-5, calls 1–5 | 33/35 | 33 | 1 each | 2 each | 0 | 1 each | 0 |

- v2-3 calls 1–4: the only uncovered text is the opening phrase "It is expected that the Technical Coordinator. will" (call 2 stops at "Coordinator.") (intro wording).
  The location and reporting content of that paragraph is quoted. So their "partial" status is a wording effect, not an omission.
- v2-3 call 5: the 16 duty lines are genuine omissions (no verbatim quote, no paraphrase). Its one intro span is the same phrase.
- v2-5, all calls: the second reporting paragraph ("The Technical Coordinator. will perform his/her duties in coordination…") is a
  genuine omission. The sentence "The Coordinator shall submit deliverables…" is also a genuine omission in calls 1–5 (no answer quotes it).
  The frozen C_REPORTING check fails for v2-5 for the same reason. Coverage adds the paragraph-level omission that the frozen checks do not test.

**gpt-4o-mini audit (`tests/fixtures/requirements_v2_technical_coordinator_20/audit`), 10 calls**

| Calls | Quoted | All text quoted | Omitted units | Genuine spans | Paraphrase | Intro | Duty omissions |
|---|---|---|---|---|---|---|---|
| all 10 | 15–17/35 | 0–2 | 18–20 | 18–20 | 0–2 | 0 | 16 in every call |

- Every audit call omits all 16 duty lines (genuine), and two competency lines ("Good understanding…", "Ability to support system testing…"),
  which are also genuine.
- The paraphrase spans are the two reporting paragraphs restated in the answer's own words (for example "The Coordinator will work at
  the MoNE office in Ramallah…"). They are not credited under the verbatim rule, but they are not genuine omissions of content.
- Punctuation-only spans (15–16 per call, the final full stop of covered units) are reported and excluded from the omission counts.

## What this does NOT show
- It does not show that extraction accuracy improved. Replay measures what stored answers account for, and runs no new prompt.
- The 0.9 coverage threshold and the 0.6 paraphrase share were fixed before the replay and are not tuned on it. The paraphrase rule
  is a word-share threshold: a loose restatement that shares fewer content words is classed `genuine_omission` (a test pins this). So
  `genuine_omission` may include paraphrases that are too loose to match, and it is a review candidate, not a finding.
- The intro-wording lexicon is explicit and small. A lead-in phrase outside it is classed as genuine. None of the stored answers drops
  only the lead-in of a different kind, so the lexicon is tested on synthetic text only.
- Semantic completeness is NOT assessed. A covered unit can still lose a short clause that changes its meaning. The prototype flags
  such units for review (`clause_review_required`) and reports the exact missing text. It does not decide the meaning.
- Units are physical lines. A three-sentence paragraph line is one unit, so an answer that quotes two of its sentences is partial.
  Sentence-level units were not built, because sentence boundaries in this JD are ambiguous ("Technical Coordinator.").
- The annotation of headings and background is hand-reviewed for TC20 only. B-case answers (English and Arabic) are not replayed.
- Arabic wrapped continuations are never joined, so a wrapped Arabic requirement becomes separate units (known limit, tested).
- The inventory-guided preview is rendered from the pinned baseline (system sha `21a2f942…`, user sha `ff6b334a…`, both verified). It is
  not sent and not validated against any run. A pilot needs an approved cap before its first request. Measured cost for the TC20
  repeat calls is $0.039179 per call on average (n=10, gpt-4.1), not the $0.08 worst-case reservation.
