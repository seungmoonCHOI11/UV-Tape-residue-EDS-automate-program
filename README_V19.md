# V19 – Verification / ROI / Score Calibration Update

This version is a patch on the existing v18.1 project. Existing Dashboard, Verification, Gallery, AI Analysis, Reports, Supabase, R2, re-analysis, project loading, and upload flows are preserved.

## Verification UI
- Verification now shows **SE / C / O only**.
- C and O panels are enlarged for visual inspection of small residue pixels.
- Yellow Local Ring is completely removed from generated overlays and the UI.
- ROI is displayed as an irregular contour/mask, not a rectangular bounding box.

## ROI detection
- Nearby fragments are merged before selecting the primary physical residue.
- Broad SEM shadow/background is filtered using signed local-background contrast.
- Large bright/dark residues get a direct signed-background candidate path so their full body is not reduced to a small edge fragment.
- Bottom metadata/scale-bar area and image border remain excluded.

## Score calibration
The previous CV score is recalibrated as requested:

`new_score = max(0, previous_raw_score - 15)`

Therefore:
- previous 85 → new 70
- previous 90 → new 75
- previous 100 → new 85

The main automatic RESIDUE gate starts at **70/100**, with ROI quality and C/O evidence safeguards. A high signal score with poor ROI quality becomes REVIEW.

A bounded SEM visual-evidence bonus is applied only when the ROI is coherent/high quality and at least one C/O channel supports the candidate. This is intended to prevent obvious SEM residues with a large shadow from being unfairly downgraded while keeping weak candidates below the residue threshold.

## New Analysis crash fix
The `parseSelection()` helper used by the New Analysis page was scoped inside the main App component even though `UploadPage` is a separate component. That caused a browser-side `ReferenceError` when opening New Analysis. V19 moves the helper to module scope.

## OpenAI role
OpenAI remains an optional ROI-guidance/experiment-level analysis component. Final per-Point classification remains deterministic CV + Human Verification; OpenAI is not the authoritative per-Point classifier.
