# v23.7.46

This release changes only the C/O residue decision threshold and keeps the v23.7.45 point-failure continuation behavior.

## Classification
- RESIDUE: C ROI/Global >= 2.50x **and** O ROI/Global >= 3.00x
- AMBIGUOUS: C >= 2.00x and O >= 2.00x, but either residue threshold is not met
- NON-RESIDUE: either C or O < 2.00x

The score is normalized per element so that C=2.50x and O=3.00x are both exactly the 70-point residue boundary.
