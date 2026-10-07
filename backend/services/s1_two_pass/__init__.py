"""
S1 two-pass architecture (s1_requirement_spec_v4), offline foundation: Pass A (target) and Pass B (context)
wire schemas and strict validators, deterministic assembly with fail-closed rules F1-F6, structural conflicts
and fail-closed S2 views. No prompt and no model call exist here yet. SHADOW ONLY: nothing in production imports
this package. The QC-side agreement lives in the QC service package and receives plain data (s1_reading).
"""
