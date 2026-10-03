"""
S1 RequirementSpec foundation (shadow only).

Turns the D-01 experience criteria of one job into s1_requirement_spec_v2
artifacts (schema.py) and deterministic RequirementSpec views for S2
(assemble.s2_views). Nothing in production imports this package; nothing is
persisted; S2 is never executed here.

Modules: schema, durations, jd_text, criteria (D-01 enumeration mirror),
validator, assemble, classifier.
"""
