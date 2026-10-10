# Regression fixture: JOB-2026-0121 "Technical Coordinator 20"

Source: `evidence.json`, the stored evidence supplied for this investigation, copied byte for byte.
It contains the JD (`description`), the original model answer (`pipeline.raw_response.text`, with
`pipeline.raw_ai_output` as its parsed form), the actual call metadata (`usage`) and the pipeline record
that the live job showed (`pipeline.extraction`, `pipeline.provenance`, `pipeline.review_records`).

## Verified identities

| Item | Value | Check |
|---|---|---|
| JD SHA-256 | `c0c7132c6bd1373ad1be5fed31db87932dda7717c4a57023b189335e1c02d265` | equals `job_description_sha256` and `review_records.injection.jd_sha256` |
| Raw answer SHA-256 | `5c051eedcb963da3850d52174f09bcdad6e2489e0ae0368fa2b7aaf9e6529dc7` | equals `pipeline.raw_response.sha256` |
| Parsed answer SHA-256 | `aff4ced71c91e30d2bc4f898f69939e442d09764ae7dd92a3667a8dc9c8b1753` | equals `review_records.injection.raw_ai_sha256` (sorted-key JSON) |
| Extraction prompt | `criteria_extraction_v2-3`, SHA-256 `21a2f9420c12e1a43579c6817b6a8601547d92b653cd432a693ef590b784b43b` | equals `prompt_candidates/criteria_extraction_v2-3/criteria_extraction_v2-3.txt` and the migration 108 note |
| Model | requested and returned `gpt-4o-mini-2024-07-18` | `usage[0].metadata` |
| Settings | temperature 0.1, max_tokens 6000, response_format json_object, timeout 90 s | `usage[0].metadata.settings` |
| Usage | prompt 6485, completion 962 tokens | `usage[0]` |
| finish_reason | `stop` (not `length`: 962 of 6000 tokens used) | `usage[0].metadata` and `pipeline.raw_response` |
| Output | 15 items (skills 4, experience 4, education 1, soft_skills 6); 0 `from_responsibilities`; no model warnings | counted from the raw answer |

The prompt identity was checked offline. The prompt row that was active on the VPS cannot be read from here,
so the metadata above is the only evidence of what ran.

## What the stored answer is

Every `source_text` and `importance_cue` is copied verbatim from the JD (checked character for character), and the
raw answer and parsed answer describe the same items. The frozen parser accepts it with no issues, and the frozen
pipeline gives `status: draft`, readiness `ready`, no reasons (reproduced offline, see the regression tests).
So the "Ready" result is what the system does with this answer; the answer itself is incomplete.

See `expected_extraction.md` for the item-by-item comparison and the classification.
