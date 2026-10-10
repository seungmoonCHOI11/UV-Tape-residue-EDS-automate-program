# v23.7.48 — C Residue Gate 2.40×

This release keeps the reliable 27-Point save/retry/reconciliation pipeline from v23.7.47 and changes only the Carbon residue threshold.

## Classification rule
- RESIDUE: C ROI/Global >= 2.40x **and** O ROI/Global >= 3.00x
- AMBIGUOUS: C >= 2.00x and O >= 2.00x, but either residue threshold is not met
- NON-RESIDUE: either C or O < 2.00x

## Score calibration
- C 2.40x maps to the 70-point Residue boundary.
- O 3.00x maps to the 70-point Residue boundary.
- Existing saved automatic/Human ROI ratios are re-evaluated with the new thresholds when loaded.

## Data-integrity behavior retained
- Fixed W/P manifest slots
- Transient save retry
- Per-Point save verification
- Final 27/27 DB/R2 reconciliation
- Missing-Point recovery
- Existing Human ROI preservation
