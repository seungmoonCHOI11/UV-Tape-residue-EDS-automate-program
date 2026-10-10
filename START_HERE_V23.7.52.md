# START HERE — v23.7.52

## What changed

### 1. Verification crash hardening
- Workspace point records are normalized before rendering.
- Legacy/malformed `features` and `assets` payloads are converted to safe objects.
- Asset values that are objects/arrays are normalized to usable URLs/paths instead of calling string methods directly.
- Verification is protected by a render error boundary, so a bad legacy point should show an internal Verification error instead of crashing the entire application.
- Verification can still open when all points already have Human Verified labels.

### 2. MAIN engineering scope
MAIN condition evaluation now uses only:
- **W1 = Corner**
- **W4 = Edge**
- **W5 = Middle**

The expected MAIN completeness is **27 points per condition = W1/W4/W5 x P1-P9**.

### 3. W9 Reference / Other
- W9 is excluded from MAIN condition ranking, residue incidence, location spread, and MAIN completeness.
- W9 is shown in a separate Reference / Other section.
- W9 data is grouped by **substrate type**. Example: W9 Si is compared only with W9 Si; W9 SiCN remains a separate group.
- W2/W3/W6/W7/W8 are counted as Other wafers.

### 4. Wafer-based substrate assignment
The substrate selector now means **Wafer substrate** rather than measurement Point substrate.
For example, setting `W9 = Si` assigns Si to all P1-P9 measured on W9.

### 5. Engineering Summary exports
Summary PPT/PDF now have 5 sections/pages:
1. MAIN condition summary
2. Power x Time matrix (MAIN)
3. W1/W4/W5 position comparison
4. Evaluation basis / data quality
5. W9 Reference / Other grouped by substrate

### Classification rule
- Residue: **C ROI/Global >= 2.40x AND O ROI/Global >= 3.00x**
- Ambiguous: both >= 2.00x but one of the Residue gates is not met
- Non-residue: either C or O < 2.00x
- Priority: **Human Verified > current C/O rule > legacy stored label only when ratios are unavailable**

## Deployment
This release changes both frontend and backend. Redeploy **Vercel and Render**.

Existing database records are not deleted or rewritten. Old W9 records keep the substrate metadata they already contain; future W9 uploads should explicitly set W9 substrate (default UI: Si).
