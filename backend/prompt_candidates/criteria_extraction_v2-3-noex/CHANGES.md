# criteria_extraction_v2-3-noex (offline control candidate)

Status: **candidate only**. Not registered in `ai_prompts`, not activated, not run against any model.
SHA-256 `764d2ee4ad8a523e67ef27b143b41d01538853d8f7d3c8921f3adf4430b87d90`. Base: `criteria_extraction_v2-3` (`21a2f942…b784b43b`).

## Purpose

A control for the TC20 evaluation: v2-3 with only the worked-examples block removed, to see whether the examples
contribute to the stored failures (see `tests/fixtures/requirements_v2_technical_coordinator_20/EVALUATION.md`, section
on the example-removal control). It can't prove a cause on its own; it measures one variable.

## Change

- Removed: the span from the line `EXAMPLES (illustrative only; never copy them into your answer)` to the end of the file,
  which holds the four examples and the ADDITIONAL EXAMPLES block (12,341 bytes, 84 lines; its SHA-256 is in MANIFEST.json).
- Kept byte-for-byte: everything before that line, including the untrusted-text paragraph, the output format block, the
  JSON shapes and enums, and rules 1–13 with their inline "for example" illustrations (those illustrate a rule, not an example).

The variant is an exact byte prefix of v2-3; the offline tests and the executor verify this on every run.
