# v23.7.49 — Human ROI classification consistency fix

## Current classification
- RESIDUE: C ROI/Global >= 2.40x **and** O ROI/Global >= 3.00x
- AMBIGUOUS: C >= 2.00x and O >= 2.00x, but either residue threshold is not met
- NON-RESIDUE: either C or O < 2.00x

## Fix
The Verification screen now derives the Human ROI label directly from the current Human ROI C/O ratios, using the same thresholds as the score. This avoids stale labels saved under older rules.

Example: C=2.59x and O=4.45x is **Residue** under the current rule and cannot display as Ambiguous merely because an older saved `human_roi_rule_result` remains in the database.
