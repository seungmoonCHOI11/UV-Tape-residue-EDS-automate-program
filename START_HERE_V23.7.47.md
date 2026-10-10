# v23.7.47 — Reliable 27-Point Save Pipeline

This version keeps the v23.7.46 classification rule (C >= 2.50x, O >= 3.00x) and rebuilds the analysis/save pipeline for data integrity.

## What changed

1. **Manifest lock**
   - The configured sequence is fixed before analysis (for example W1-P1 ... W5-P9).
   - A failure at W1-P2 does not allow W1-P3 to be stored in the W1-P2 slot.

2. **Transient save retry**
   - R2/Supabase persistence retries the same Point up to 3 times.
   - Backoff: 1s -> 2s -> 4s.
   - The complete Point write is idempotent, so a lost response after a successful server commit is safe to repeat.

3. **Write verification**
   - After save, the backend confirms the Point row, analysis row, asset rows, and R2 image objects.

4. **Final reconciliation + repair**
   - After the first pass, the expected manifest is compared against the actual Supabase/R2 dataset.
   - If a Point is incomplete, already-generated local images are re-saved first.
   - If no worker result exists, only that Point is selectively re-analyzed.
   - The final status is `completed` only when every expected Point is verified.

5. **No silent image-only shift**
   - If a PDF has no searchable Point labels and its 3-page group count differs from the configured count, analysis stops instead of guessing and shifting every later Point.

6. **Human ROI preservation**
   - Save retry/re-analysis no longer writes null Human Verification fields over existing values.
   - Existing Human ROI feature data is preserved when machine analysis updates the analysis row.

## Validation

Backend targeted tests cover Point failure continuation, C/O thresholds, transient `Errno 11` retry, exact missing-Point reconciliation, manifest slot stability, and image-only mismatch protection.
