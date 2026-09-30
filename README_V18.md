# v18 Hybrid ROI Update

- CV/OpenCV remains the individual Point classifier.
- OpenAI vision is used only as a second-stage coarse ROI proposal for uncertain points by default.
- The AI returns normalized boxes; OpenCV performs the actual irregular mask segmentation inside those boxes.
- Bottom SEM/EDS acquisition metadata and scale-bar area remain excluded.
- Image borders are excluded.
- Yellow ring is visual-only and is never used as the classification baseline.
- Residue scoring remains ROI vs the whole analytical image, not ROI vs local ring.
- Existing Human Verification results are preserved by the existing re-analysis path.

Set `OPENAI_ROI_MODE=all` only when you intentionally want vision-assisted ROI proposals for every Point.
