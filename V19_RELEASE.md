# V19 Release

- Verification UI redesigned: SE/C/O only; C and O enlarged for visual inspection.
- Yellow Local Ring removed from generated overlays and UI.
- ROI remains an irregular pixel/contour mask; nearby fragments are merged before scoring.
- Added ROI quality metric. High residue score with low ROI quality is forced to Review.
- Score calibration: displayed score = previous raw score - 15 points (floor 0). Thus previous 85 -> new 70; previous 90 -> new 75.
- RESIDUE gate starts at new score 70 with minimum C/O evidence and ROI quality.
- Bottom metadata/scale area and image border remain excluded from analysis.
- OpenAI remains optional ROI guidance only; final point classification remains OpenCV/CV + Human Verification.
- Existing project/R2/Supabase/re-analysis/data-loading features are preserved.
