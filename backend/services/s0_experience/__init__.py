"""
S0 v2 — CV-level experience structure (anchor-and-attach). SHADOW ONLY.

Separate from the legacy CVFacts extractor, which the production D-01 /
local-matcher path keeps using unchanged.
"""
from services.s0_experience.schema import S0_SCHEMA, S0_VERSION, S0Document, S0Entry

__all__ = ["S0_SCHEMA", "S0_VERSION", "S0Document", "S0Entry"]
