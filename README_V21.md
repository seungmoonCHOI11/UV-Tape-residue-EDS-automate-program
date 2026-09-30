# V21 Release Notes

- Verification UI redesigned to a reference-style layout: SEM + Full EDS on top, larger C/O maps below, Analysis Result panel on the right.
- Verification hides N/Si panels while preserving backend data fields.
- New analysis/re-analysis outputs do not create or use the Local Ring. Legacy stored overlays may continue to show the old ring until Re-analysis, as intended.
- Score remains a true 0–100 score with piecewise calibration: previous 85 -> new 70; previous 100 -> new 100.
- Decision bands: Residue >= 70, Ambiguous 60–69, Non-residue < 60.
- A high score with low ROI quality is downgraded to Ambiguous rather than auto-Residue.
- Irregular residue mask, component merging, shadow filtering, global ROI comparison, and scale/info exclusion are preserved.
- Existing project/R2/Supabase/Human Verification/Re-analysis workflows are preserved.

- If no defensible SEM candidate is found, the automatic state is Ambiguous rather than Non-residue.
