# Expected extraction: JOB-2026-0121 "Technical Coordinator 20"

Each row is checked against the JD in `evidence.json` (`description`) and against the rules of
`criteria_extraction_v2-3`. Quotes are verbatim from the JD. "Stored answer" is `pipeline.raw_response.text`.

## A. Definite: the stored answer breaks an explicit prompt rule or omits JD content

| # | Expected | JD quote (verbatim) | Rule | Stored answer |
|---|---|---|---|---|
| D1 | 16 `from_responsibilities` experience items, one per duty line of the Responsibilities section (listed in the regression test) | "Support the implementation, rollout, operational stabilization, and continuous improvement of ABRS services and related digital platforms." … "Contribute to strengthening MoNE’s capacity to independently support, maintain, and enhance ABRS services and related digital platforms." | 7 | none: 0 `from_responsibilities` items |
| D2 | A skills item for the first competency line | "Good understanding of business applications, digital platforms, and software support processes." | 1 | omitted |
| D3 | Item(s) for the second competency line (rule 4 may split the comma list: see A5) | "Ability to support system testing, troubleshooting, issue tracking, and operational follow-up activities." | 1 | omitted |
| D4 | Item(s) for the third competency line (see A5) | "Ability to communicate effectively with users, understand their requirements and issues, and support their resolution in coordination with relevant stakeholders." | 1 | omitted |
| D5 | Experience item keeps ONE item with `alternatives` = ["ICT systems support", "business applications", "digital platforms", "software implementation projects"] and `min_years` 1 | "1–3 years of professional experience in ICT systems support, business applications, digital platforms, or software implementation projects." | 4, 6 | `min_years` 1 correct; `alternatives` null |
| D6 | The preferred experience item keeps its "or" options as `alternatives` ["business process documentation", "system integrations", "databases", "reporting tools", "enterprise applications"] | "Familiarity with business process documentation, system integrations, databases, reporting tools, or enterprise applications is an advantage." | 4 | `alternatives` null |
| D7 | "Knowledge of the local business and regulatory environment" in `domain_knowledge` (subject-matter knowledge) | "Knowledge of the local business and regulatory environment is an advantage." | 5 | in `experience` |
| D8 | A non-scoreable `location` condition | "It is expected that the Technical Coordinator. will work at the MoNE office in Ramallah and as needed, at other MoNE offices in the West Bank…" | 9 | `non_scoreable_requirements` empty |
| D9 | `informational_items` with category `reporting_line` for the reporting statements | "The Coordinator shall submit deliverables to the IPSD II Technical Resident Advisor and the IPSD II Project Director for review and approval." and "The Coordinator will maintain regular communication with the IPSD II Technical Resident Advisor and the IPSD II Project Director…" | 9 | `informational_items` empty |
| D10 | Three soft-skill items: communication; coordination; teamwork skills (the AND list is separate items) | "Strong communication, coordination, and teamwork skills." | 4 | one item, "Strong communication, coordination, and teamwork skills" |

Model-side note (not a definite omission): the stored answer has `warnings: []` although the grouping rules in
rule 4 leave some groupings open (section B). Whether the answer should have warned is a judgement, not a rule violation.

## B. Ambiguous: grouping or category choices the prompt does not settle

| # | Item | Choices | Why ambiguous |
|---|---|---|---|
| A1 | "JavaScript" (preferred) | Keep the broader requirement in the text: "Basic knowledge of web technologies and scripting languages (particularly JavaScript)"; or keep "JavaScript" as the item and add the broader statement as a second item | The source sentence is "Basic knowledge of web technologies and scripting languages, particularly JavaScript, is considered an advantage." The stored text keeps only the example. Rule 1 says not to drop a requirement; whether the example is the requirement is a wording choice. |
| A2 | "Good analytical and problem-solving abilities" | one item, or two (analytical; problem-solving) | Rule 4 splits independent requirements joined by "and". Whether these two are independent is not settled. |
| A3 | "Good documentation and reporting skills" | one item, or two | Same as A2. |
| A4 | "Strong attention to detail and commitment to accuracy and quality" | one item, or two | Same as A2. |
| A5 | "Strong organizational skills with the ability to plan, prioritize, and manage multiple tasks effectively"; "Ability to work effectively under pressure and meet deadlines in a dynamic operational environment"; the D3 and D4 competency lines | one item each, or one per activity (rule 4 treats a comma list as separate items) | The prompt's comma rule covers lists of nouns; these are clauses describing one ability. |
| A6 | "Experience in supporting, testing, implementing, or operating business systems, digital services, or e-government platforms is an advantage." | one item with `alternatives` for each "or" list, or one item without | Two "or" lists in one sentence (activities; platforms). Which options belong in `alternatives` is not settled. Stored: one item, no alternatives. |
| A7 | Category of the D6 familiarity item | `skills` (tools, systems: rule 5) or `experience` (stored) | Rule 5 gives "skills" tools and systems; the item lists tools and systems, but is worded as familiarity. |
| A8 | Category of the D2 competency line | `skills` or `domain_knowledge` | "understanding of … processes": skills vs subject-matter knowledge. |
| A9 | Coordination with the MoNE IT focal point and with the PIA | `informational_items` as `reporting_line`, or not an item | Rule 9 defines reporting_line; the coordination statement may or may not be a reporting arrangement. |

## C. Verified correct in the stored answer

- No invented requirement: all 15 items map to JD text (rule 1). All `source_text` and `importance_cue` values are verbatim (rule 2), checked character for character.
- Importance: preferred items are the four "is an advantage" / "is considered an advantage" sentences; cues are verbatim.
- "Good command of Arabic and English …" is two items (Arabic; English), as rule 4 requires.
- The Bachelor's degree is one education item with its alternatives.
- The 1–3 years experience item has the correct `min_years` (1) and `subject`.
- Category weights 30/20/30/20 with zero for categories without Required items (rule 11).
- No output was truncated: 962 of 6000 completion tokens, finish_reason `stop`.
