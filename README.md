# UV Tape Residue EDS Analysis System — v8

## v8 memory-safety change

v8 changes the PDF/SEM/OpenCV analysis architecture after Render Free (512 MB) memory-limit restarts were observed during multi-Point processing.

### Core change

The FastAPI process no longer imports or holds PyMuPDF/OpenCV/numpy objects for the Point-analysis loop.

For every Point:

1. FastAPI creates a tiny JSON job description.
2. `point_worker.py` is launched as a separate OS process.
3. The worker opens only the required PDF pages for that Point.
4. Pages are rendered one at a time at a capped resolution.
5. Source pages and compatibility crops are saved to disk.
6. Preliminary OpenCV features are calculated.
7. One small JSON result is returned through a result file.
8. The worker process exits.
9. The operating system reclaims the worker's native PyMuPDF/OpenCV memory before the next Point.

The numerical-library thread counts are limited to one thread per worker.

### Mapping

The source structure remains:

- 3 consecutive PDF pages = 1 Point
- Conditions are entered by the user in PDF order
- Wafer/Point sequences are generated from those conditions
- Page count is validated before analysis

Example:

- 150W / 30s / Wafers 1,4,5,9 / Points 1-9 → 36 Points → 108 pages
- 350W / 120s / Wafers 1-9 / Points 1-9 → 81 Points → 243 pages

### Important analysis limitation

The current vendor PDF crop contains a combined Element Maps panel. v8 does **not** claim that this panel has been scientifically separated into independent C and O maps. The preliminary CV result therefore remains a prototype signal. Separate C/O map extraction or vendor-exported elemental maps should be implemented before using the CV score as a validated residue classifier.

### Frontend

The existing Vercel frontend remains compatible with the `/api/upload` and `/api/jobs/{job_id}` flow. The progress modal continues to show upload, analysis, and database stages.

### Deployment

The backend remains a Render Web Service for now. v8 is specifically intended to test whether per-Point process isolation keeps the service below the 512 MB Free memory limit. If the real dataset still exceeds the limit, the next architectural step is a dedicated Render Background Worker with more memory rather than additional small patches.
