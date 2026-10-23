"""Requirements V2 item-level candidate scoring.

    calc.py        exact, deterministic arithmetic (no model, no database, no floating point)
    evaluator.py   the model call that classifies each requirement against the CV, and the validation of its output
    service.py     persistence of runs, evaluations and human resolutions; gate results derived from the stored evaluations

The model decides only each item's RESULT and its supporting evidence. Every number is computed by calc.py.
"""
