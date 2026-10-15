"""
S2 Phase 1 — semantic classification of validated S0 experience entries
against one stored RequirementSpec. SHADOW ONLY: no production module imports
this package. See classifier.py for the contract and call flow.
"""
from services.s2_experience.classifier import S2Result, classify_criterion

__all__ = ["S2Result", "classify_criterion"]
