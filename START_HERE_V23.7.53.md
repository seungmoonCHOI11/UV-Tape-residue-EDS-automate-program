# v23.7.53

## What changed
1. Fixed Verification opening crash: `Cannot access 'A' before initialization`.
   - Cause: `Review()` declared a local `hasHumanROI` constant while also calling the global `hasHumanROI()` helper on the same render.
   - The local binding shadowed the helper and caused a JavaScript temporal-dead-zone ReferenceError.
   - Renamed the local state to `humanRoiSaved` so Verification renders normally.

2. Centered the initial `Project data loading` card on the actual browser viewport.
   - Initial loading no longer inherits the 235 px sidebar/main offset.
   - Loading overlay is fixed to the full viewport.

## Comparison scope retained
- MAIN: W1 = Corner, W4 = Edge, W5 = Middle
- W9: Reference / Other, separated by substrate
