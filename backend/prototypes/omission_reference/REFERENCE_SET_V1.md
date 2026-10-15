# Reference set omission-reference-set-v1

**Scope.** Version 1 labels the reviewed parent rows and decided child units listed below. It does **not** validate the whole 114-row candidate sheet. 70 candidate rows are **unreviewed**; they are listed in `reference_set_v1_manifest.json` and are not part of the reference labels.

## Counts

| item | count |
|---|---|
| candidate sheet rows (Claude-generated candidates) | 114 |
| reviewed parent rows (accepted as version-1 labels) | 44 |
|   resolved | 42 |
|   resolved by children | 2 |
| parent rows unresolved | 0 (RQ-003 was superseded by its batch-ten background decision) |
| decided child units | 8 |
| **unreviewed candidate rows (NOT validated)** | **70** |

## Submissions and amendments (SHA-256)

- `CONFIRMED_queue_Asaad-Hijjah_2026-10-15.csv` (5 rows): `1be5e1906c528a74aef6b77a218858924089536f58435049bd470d215f6f3fe8`
- `CONFIRMED_queue_Asaad-Hijjah_2026-10-15_batch10.csv` (1 rows): `b8a8e1296682943790b677fb618849a8a65ae5ef606210c9dc0e52eaf41e2aa6`
- `CONFIRMED_queue_Asaad-Hijjah_2026-10-15_batch2.csv` (5 rows): `eeb385eb4ae3dd6f9258024b813e82a2f54d75af5c833caa544fea973da59b90`
- `CONFIRMED_queue_Asaad-Hijjah_2026-10-15_batch3.csv` (5 rows): `deee39de2d0b4a1ad6907c28323073c4ca70aa2f5e50e95e07a7fc813492b580`
- `CONFIRMED_queue_Asaad-Hijjah_2026-10-15_batch4.csv` (5 rows): `a967319577e67909216ed11c0b065e99ff1dd43cfd49c4521b67fff07efc62c0`
- `CONFIRMED_queue_Asaad-Hijjah_2026-10-15_batch5.csv` (5 rows): `e7d5134305985361b24fdd89eea6fff47787fc73adfc34cd01c88e21b1d81081`
- `CONFIRMED_queue_Asaad-Hijjah_2026-10-15_batch6.csv` (5 rows): `2fcebe9825059dec9f6a65fa826a9bff8ab34913304f7bde620b65409cf09726`
- `CONFIRMED_queue_Asaad-Hijjah_2026-10-15_batch7.csv` (5 rows): `fda9d54b2e819d5b5a243f50432bfc669d1aad21105462626059e4dca7effece`
- `CONFIRMED_queue_Asaad-Hijjah_2026-10-15_batch8.csv` (5 rows): `119eba47f02d87e6df19b3023f8db44f51cb043f6b3e4fdc932270ed68197714`
- `CONFIRMED_queue_Asaad-Hijjah_2026-10-15_batch9.csv` (4 rows): `e354ee1a2d406ad10d6d5b20968c6b4191c8203b993ac38971abaa7a775d243d`
- child: `CHILD_CONFIRMED_queue_Asaad-Hijjah_2026-10-15_RQ-029.csv`: `33895564ec5f87dc95761007a400c10b48e8b881bcd43a0de6eaa676419978cd`
- child: `CHILD_CONFIRMED_queue_Asaad-Hijjah_2026-10-15_RQ-030.csv`: `f64e59baf53fd7e55e027d63f18166c885f5026ca9661fb800be0fba24dc2f4c`
- amendment: `AMENDMENT_batch7_RQ-032_Asaad-Hijjah_2026-10-15.csv`: `75a94b75009bfb8f6d77d7dfdc25be098fd41d46bfa1e11fcb8fc1e434003979`

## Inputs (SHA-256)

- reviewer sheet: `f71cb861d4c072e6e9000077bfd29b21771da8d1de94a39a5664d63f9949c6c8`
- child units: `a01ad44b525ab1a95ba338425d8ca21f65100d9cdf3570ec89194a9e8dcb78f7`
- review queue: `050c178cd186bc1eb75b70b7df87d802d289fbf214a10461b4b0b26af1e0c53c`
- package manifest: `c01e7b69a98861efb633baab60ca8855cd53ed73627e20cf8b5ba35b54519395`
- frozen benchmark scorer (unchanged): `c4adf92b9844943ea7fe1e19dc30b899596059c4ce14730363dde2e182f98ebe`

The machine-readable manifest (`reference_set_v1_manifest.json`) lists the 70 unreviewed rows by unit id.
