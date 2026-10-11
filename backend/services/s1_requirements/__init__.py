"""
S1 RequirementSpec foundation (shadow only).

Turns the D-01 experience criteria of one job into s1_requirement_spec_v3
artifacts (schema.py) and deterministic RequirementSpec views for S2
(assemble.s2_views). Nothing in production imports this package; nothing is
persisted; S2 is never executed here. s1-6 (P4a): S1 is an independent reading
of the JD (never fed the qualifying-context analysis); S2 views are fail-closed
until a resolved context_resolution exists (the P4c agreement, not built yet).

Modules: schema, durations, jd_text, criteria (D-01 enumeration mirror),
validator, repair, assemble, classifier.
"""
