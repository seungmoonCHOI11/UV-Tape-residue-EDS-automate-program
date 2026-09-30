# V20 Score Calibration

- Final score remains a true 0–100 score.
- The previous 85-point level maps to the new 70-point level.
- The previous 100-point level remains 100.
- The previous 0-point level remains 0.
- This is **not** a flat -15 offset.
- For previous score `S`:
  - `S <= 85`: `new = S * 70 / 85`
  - `S > 85`: `new = 70 + (S - 85) * 30 / 15`
- Residue gate remains `new >= 70`, subject to ROI quality and evidence gates.
- Low ROI quality can still force `Review` even when the calibrated score is high.

Examples:

| Previous | New |
|---:|---:|
| 0 | 0 |
| 40 | 32.9 |
| 60 | 49.4 |
| 70 | 57.6 |
| 80 | 65.9 |
| 85 | 70 |
| 90 | 80 |
| 95 | 90 |
| 100 | 100 |
