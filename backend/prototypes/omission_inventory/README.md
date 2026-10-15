# Omission-coverage prototype (offline)

Scope: measures which requirement lines of a job description (JD) an extraction answer accounts for. It is an offline replay of stored
answers. It is not production code, it is not imported by the backend, and it makes no model or API calls. It changes nothing
frozen: the scorer `scripts/requirements_v2_tc20_eval.py` is reused read-only (`score_answer`, `_items`).

## Files
- `inventory.py`: source units from the ORIGINAL JD text (stable ids `<sha12>:L<line>:<start>-<end>`).
- `coverage.py`: coverage from quoted spans (union, threshold, ambiguity, verbatim gate).
- `annotations/tc20.json`: reviewed heading and background lines for the TC20 JD (exact text, keyed by the JD sha256).
- `replay.py`: reads the stored TC20 answers (`tests/fixtures/requirements_v2_tc20_repeat` and `..._technical_coordinator_20/audit`) and
  writes `results/replay.json`. Stored files are not modified (tested).
- `preview.py`: renders the proposed inventory-guided request to `preview/inventory_guided_request.txt`. It is not sent.
- `tests/`: 43 offline tests. Run: `python -m pytest prototypes/omission_inventory/tests -q` from `backend/`.

## Rules (fixed before the replay was run)
1. Units are physical lines of the original text, joined only when a line starts with a lowercase Latin letter and the line before has no
   terminal punctuation (soft wrap). Arabic is never joined (no case): a wrapped Arabic continuation is its own unit (known limit).
2. Headings and background are never recognised by length or punctuation. They must be listed exactly in a reviewed annotation for
   that JD. A stale or foreign annotation is an error.
3. A quote is credited only if it is an exact substring of the original JD (the verbatim check is unchanged). Paraphrases and
   whitespace variants get no credit; the whitespace case is flagged as a diagnostic.
4. A quote that occurs more than once in the JD is ambiguous and gets no credit. An uncovered unit that an ambiguous quote touches is
   routed to `ambiguous`, not `omitted`.
5. A unit is `covered` at 0.9 or more of its non-space characters covered by the UNION of credited quotes, `partial` above 0 and below
   0.9, `omitted` at 0. One short quote cannot cover a long unit.
6. No normalised offsets are used for any decision. The only normalisation is a diagnostic string comparison.

## Replay results (stored answers only; no new calls)
Coverage is over 35 requirement units of the TC20 JD (9 headings and 3 background lines excluded).

| Source | Calls | Duty omissions (of 16) | Other omissions | Partial |
|---|---|---|---|---|
| gpt-4.1 repeat, v2-3 | 5 | 0, 0, 0, 0, **16** (call 5) | 0 | 1 in each call (the location and reporting paragraph) |
| gpt-4.1 repeat, v2-5 | 5 | 0 | 1 each: "The Technical Coordinator will perform his/her duties in coordination..." | 1 in each call |
| gpt-4o-mini audit, v2-3 and v2-4 | 10 | 16 in all 10 | 2 competency lines and the location and reporting paragraphs | 0 to 2 per call |

- The 16-duty omission in repeat v2-3 call 5 is reproduced exactly (the same 16 lines as `expected_labels.json`).
- Coverage and the frozen OR/AND checks disagree in places (for example v2-3 call 1 covers 34 of 35 units yet fails C_AND). They are
  reported separately, and the OR/AND result is not coverage.
- Coverage found an omission that the frozen checks do not test: repeat v2-5 never accounts for the second reporting paragraph (with
  "keep them informed"). This is a finding to review, not a fix.
- Partial units are mostly one paragraph line holding three sentences, of which the answers quote two. The threshold is correct as
  designed, but it shows that the unit granularity is paragraph-level for these lines. Splitting by sentence was not implemented, because
  sentence boundaries in this JD are ambiguous ("Technical Coordinator.", "IPSD II").

## What this does NOT show
- It does not show that extraction accuracy improved. Replay measures what stored answers account for; it runs no new prompt.
- It does not verify the threshold (0.9) against labelled data. The value was chosen before the replay, not tuned on it.
- It covers only TC20. The B-case answers (Arabic and English) have no reviewed heading annotation and are not replayed.
- The preview is not validated against the active prompt. Activating any prompt, or making any pilot call, needs approval first, and a
  pilot needs an approved cap before its first request. Measured cost for the TC20 repeat calls is $0.039179 per call on average (n=10,
  gpt-4.1), not the $0.08 worst-case reservation.
