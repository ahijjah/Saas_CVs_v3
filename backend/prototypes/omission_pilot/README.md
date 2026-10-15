# Omission pilot: protocol, dry run and fake-client tests (no paid call)

- `PROTOCOL.md`: the pre-registered design, outcomes, guards, stop rules, thresholds, call count, reservation and freeze list.
- `pilot.py`: builds both complete requests (CONTROL = pinned v2-3, INVENTORY = the same plus the marked block), freezes their hashes,
  computes the order and the worst-case reservation, and holds the analysis and verdict logic. Its CLI supports `--dry-run` only. The
  library function `run_pilot` needs a caller-supplied client and an approval record. There is no default client.
- `tests/test_pilot.py`: offline tests with a fake client (34 tests). Run from `backend/`:
  `python -m pytest prototypes/omission_pilot/tests -q`.
- `results/dryrun.json` and `results/dryrun.txt`: the dry-run output (order, hashes, reservation, cap, justification, thresholds).

Nothing here calls a model. The cap is NOT approved. Reproduce the dry run with `python prototypes/omission_pilot/pilot.py --dry-run`.
