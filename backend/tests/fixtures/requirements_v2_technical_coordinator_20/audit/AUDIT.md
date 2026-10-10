# Audit: TC20 evaluation (v2-3 vs v2-4) and the prompt worked examples

Status: **partial.** The prompt and example audit is complete. The run audit (provenance and score reproduction) could NOT be
performed: the three files requested (`calls.jsonl`, `manifest.json`, `scored.json` of the TC20 run) are not in this
environment. The upload folder contains only the files listed in section 0. Nothing below claims a run result as verified.

No paid call, no change to the active prompt, production behaviour, the frozen parser, the labels or the scoring.

## 0. What was present, and what was not

| File (upload) | sha256 | What it is |
|---|---|---|
| `5634a657-technical_coordinator_v2_evidence.json` | `86a9abe7991ac55899296eb37e2133c5a51280c6d0067b106269bd8777b56590` | the stored TC20 evidence (already in the repo as `evidence.json`, byte-identical) |
| `02efa960-req_v2_calls.jsonl` | `157b91eff3846a01c3eb818522ea24d947e7417ec7fee83db7463d8ca5b0e4cd` | 12-case benchmark calls (24 rows, run1/run2); not TC20 |
| `7e3511c3-calls.jsonl` | `4beac81b7aa08dfa6d12e68014acf6866b19a40417735d929396d4dae6e732f4` | 12-case benchmark calls for prompt key v2-2 (24 rows); not TC20 |
| `c55265a4-req_v22_results.json` | `044d08272edc62e581a3eadd403c9780ae2db1ed4ab1682e0baa19a5f8f4763f` | 12-case benchmark results (`req-v2-extraction-eval-2`); not TC20 |
| `fb33b985-req_v2_results.json` | `4dd691916a43bd7c02538e8b147065e5fe075ad4a75b092f552576d481193982` | 12-case benchmark results (`req-v2-extraction-eval-2`); not TC20 |

Missing: the TC20 `calls.jsonl` (10 rows), the TC20 `manifest.json` and the TC20 `scored.json`. Without them the following
cannot be checked: the provenance (labels sha `6a87da80…`, JD sha `c0c7132c…`, prompt shas `21a2f942…`/`dd2651bc…`,
user-message sha `ff6b334a…`), the 0-success/zero-answer status, the per-check counts, and whether `scored.json` is
reproduced by the frozen scorer. **To complete section 1, re-upload the three TC20 files.** The reproduction will re-score
each saved answer with the unchanged `score_answer` and compare every check, count and consistency field with `scored.json`.

## 1. Provenance and reproduction: NOT PERFORMED

Blocked on the missing files (section 0). No score is reported here as reproduced.

## 2. Partial coverage, one claim at a time

Each claim is listed with what the prompt says (cited), what is confirmed by the prompt, and what remains open. The claims
themselves come from the brief; they are not verified against the answers (section 1 is blocked).

### 2a. User communication is extracted; two competency lines remain missing

- The JD lines (in `evidence.json`): "Good understanding of business applications, digital platforms, and software support
  processes." and "Ability to support system testing, troubleshooting, issue tracking, and operational follow-up activities."
- Prompt, confirmed: rule 5 (v2-3 line 70) defines skills as "technical and functional **abilities**, tools, systems, methods,
  and languages", and soft_skills (line 74) as "behavioural and interpersonal **traits**". An "Ability to …" sentence fits both
  categories, so the prompt does not decide where it goes.
- Prompt, confirmed: no worked example contains an "Ability" statement (census `ability_statements` = 0 in v2-3 and v2-4).
- Prompt, confirmed: rule 4 (line 56) splits a comma list joined by "and" into separate items, but the only split examples are
  languages and tools (line 64 "Spanish, French and German"; Example 1 "Photoshop and Illustrator"; Example 3 "Word وPowerPoint").
- Hypothesis H2 (open): the communication line matches the soft-skill vocabulary in rule 5 (communication), so it is kept;
  the other two lines have no such vocabulary and no example, so they are dropped. Testable with the saved answers (blocked).

### 2b. Reporting appears but is routed to the wrong list

- The JD sentence (in `evidence.json`): "The Coordinator shall submit deliverables to the IPSD II Technical Resident Advisor
  and the IPSD II Project Director for review and approval."
- Prompt, confirmed: reporting_line is an informational label (rule 9, v2-3 line 88). The census finds no example that routes
  any statement to informational reporting_line (`reporting_line_routes` = 0 in both versions).
- Prompt, confirmed (a lexical trap): rule 9 puts **document_submission** in post_hiring_conditions (v2-3 line 87). "submit
  deliverables … for review and approval" resembles that label more than it resembles reporting_line.
- Prompt, confirmed: rule 9's chooser is "Choose the list by what the statement is" (line 85); the prompt does not say what to
  do with a reporting sentence that sits inside a duties section (see 2c).
- Hypothesis H3 (open): the sentence was routed by its verb ("submit", "approve") to document_submission, not by its function
  (reporting). Testable with the saved answers (blocked). Note: this is a hypothesis about the answers, not a confirmed fact.

### 2c. Duties remain absent in all 10 answers

- Prompt, confirmed: rule 7 (line 81) is explicit: duties are output as experience items with origin "from_responsibilities".
- Prompt, confirmed: the five worked answers contain 0, 1, 2 and 3 such items (census `from_responsibilities_items` = 6 in
  total, per version). None shows a long list.
- Prompt, confirmed: v2-3 has no guidance for duty sections with sub-headings or several groups. The TC20 Responsibilities
  section has two sub-headings and 16 duty lines (`evidence.json`). v2-4 adds this guidance (v2-4 line 84); v2-3 does not.
- Prompt, confirmed: rule 7 sits between rule 6 (a long experience rule) and rule 8. It is not repeated near the examples.
- Hypothesis H1 (open): the answers take the length of the examples as the expected output size. Hypothesis H1b: the
  introductory sentence of the section ("The Coordinator will support … through the following activities") is read as the
  duty statement and the lists are skipped. Both need the saved answers (blocked).

## 3. Prompt audit: the complete v2-3 and v2-4 texts

Method: every line of both prompts was read; the five worked answers were parsed and counted (census in
`prompt_census.json`, generated by `scripts/requirements_v2_prompt_census.py`, reproducible offline).

### 3a. Confirmed contradictions

None. No worked answer contradicts a rule: each example's routing matches rule 9's table, each example's alternatives match
rule 4, each example's min_years matches rule 6, and each example's source texts are verbatim. The census confirms this for
the counts and routes; the reading confirmed it for the text.

### 3b. Confirmed ambiguities and gaps (in the text)

| # | Passage (v2-3 line) | What it says | Why it matters for the failures |
|---|---|---|---|
| A1 | line 70, skills: "technical and functional **abilities**" | skills includes abilities | "Ability to …" competency sentences can go to skills or soft_skills (2a) |
| A2 | line 74, soft_skills: "behavioural and interpersonal **traits**" | soft_skills is traits | same as A1 |
| A3 | line 71 (experience: "… **sector**, kind of work") and line 75 (domain_knowledge: "industry or subject-matter knowledge") | sector is an experience qualifier; industry knowledge is domain knowledge | "Knowledge of the local business and regulatory environment" falls under both; line 77 ("Put a requirement in exactly one category") does not say which |
| A4 | line 58, rule 4: "If the grouping is genuinely unclear, follow the exact wording and say so in warnings." | an escape hatch for unclear groupings; "genuinely unclear" is not defined | a model may treat a list it reads as one trait as unclear and keep it as one item (the soft-skill bundle). Hypothesis H4 (open) |
| A5 | line 81, rule 7 | duties are experience items; no exclusion for reporting or coordination sentences | a reporting sentence inside a duties section is claimed by both rule 7 and rule 9 (2b) |
| A6 | line 81, rule 7 | no guidance on sub-headed duty sections | 16 duty lines in two sub-groups (2c). v2-4 line 84 adds it |
| A7 | line 87, document_submission in post_hiring_conditions | a submission label that sounds like a reporting sentence | the TC20 deliverables sentence (2b, H3) |
| A8 | lines 64–65 (counts) and the five answers | the only experience-OR is a count line in prose; no answer shows an experience item with alternatives | an "or" list ending the experience sentence (TC20 line 1–3 years) has no full example. Confirmed by the census (`items_with_alternatives` for experience = 0 in all answers) |
| A9 | Example 2 (line 142: "Basic cataloguing knowledge") | domain knowledge is shown only as a generic subject | no example separates domain knowledge from experience sector (A3) |

Not a gap in v2-3, but the same in v2-4: the rules and examples are unchanged except where v2-4 adds passages (census identical
in both).

### 3c. Examples that could encourage omissions (hypotheses, not confirmed)

- The five worked answers are short and one-level: 0–3 duties, 2–4 items per category, one soft-skill-free set. The prompt
  never shows a long list or a soft_skills item (census: `soft_skills_items` = 0 in both versions). H1 above.
- No example routes a reporting statement (census). H3 above.
- No example contains an experience OR with alternatives in a full answer (A8). Supports the TC20 alternatives omission as
  a compliance failure, or as an example gap; the saved answers decide which (blocked).

## 4. Recommended next experiment (one, not run, not built)

**Example-ablation control on the TC20 JD.** Build nothing new yet. The experiment is:

- Factor: the worked examples only. Variant = v2-3 with the EXAMPLES block (v2-3 lines 99–182) removed; every rule (lines 1–97),
  the output format and the wording of the remaining text are byte-identical to v2-3.
- Input: the stored TC20 JD and metadata (the same user message as the TC20 executor).
- Model and settings: unchanged (gpt-4o-mini-2024-07-18, temperature 0.1, max_tokens 6000, json_object, no retries).
- Calls: 5, scored with the frozen labels and the same eight checks. Worst-case budget about $0.03 (within the $0.10 cap).

Why this experiment and not a new prompt: the census shows that the examples are the only place where the prompt shows output
shape, and every observed failure (duties, reporting, competency lines, experience OR, bundled AND) is a shape the examples do
not show. Removing the examples isolates that factor with one changed variable. The outcome decides the next step:

- If C_RESP, C_REPORTING and C_COMP pass more often without examples than with them, the examples are a cause of the
  omissions, and the next candidate should change the examples (not the rules).
- If they do not change, the examples are not the cause; the next step is a rule-salience experiment (rule 7 moved near the
  output format), not an example rewrite.

It addresses the observed failures only if the first outcome holds; the experiment tells us which of the two it is before any
prompt is changed. It also needs the TC20 run files to compare against the baseline, which is one more reason to re-upload them.

## 5. Open items

1. Re-upload `calls.jsonl`, `manifest.json` and `scored.json` of the TC20 run. Then section 1 and sections 2a–2c can be tested
   against the answers (H1, H1b, H2, H3, H4).
2. Approve or change the example-ablation control before any paid call.
