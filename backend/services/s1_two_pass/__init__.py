"""
S1 two-pass architecture (s1_requirement_spec_v4): Pass A (target) and Pass B (context) wire schemas and strict
validators, deterministic assembly with fail-closed rules F1-F6, structural conflicts and fail-closed S2 views.
Pass A has its pinned prompt (s1a-1.1, a baseline candidate, NOT approved; s1a-1.0 and the withdrawn s1a-1.2 are kept
for audit only, prompt_a.py) and its model-call runner (pass_a_runner.py, one main call and at most one repair call,
client injected); Pass B has no prompt or model call yet. SHADOW ONLY: nothing in production imports this package.
The QC-side agreement lives in the QC service package and receives plain data (s1_reading).
"""
