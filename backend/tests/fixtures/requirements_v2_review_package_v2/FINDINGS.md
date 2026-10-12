# Verified findings from the review package (data facts, not reviewer judgments)

## S003: a statement covered by two items is flagged as a partial quotation

- Case: B07_ar_accountant, answer v2-2 run 1 (gpt-4o-mini-2024-07-18).
- Statement (exact JD text): `الشروط: الراتب بالريال السعودي، والعمل في مدينة الرياض بدوام كامل.`
- Two items quote its two clauses:
  - `non_scoreable_requirements[0]`, category `salary`: `الراتب بالريال السعودي`
  - `non_scoreable_requirements[1]`, category `location`: `العمل في مدينة الرياض بدوام كامل`
- Both clauses are routed to `non_scoreable_requirements`, the list rule 9 assigns to salary and to location.
- Together the two quotations cover the statement's substantive text, apart from the label `الشروط:`.
- **The checker nevertheless raises `partial_quote`**, because no single item covers the whole statement.
  The paths reported are both non-scoreable items.

Status: verified from the stored answer. No checker change has been made. Test `test_the_verified_s003_split_is_flagged_as_partial_quote`
pins the current behaviour. A later change should replace that expectation with a split-across-items review item, not silence it.

Reviewer judgment on this row: not confirmed. The reviewer has not yet supplied a judgment.
