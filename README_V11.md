# UV Tape Residue EDS v11

## v11 changes
- Residue / Non-residue classification is separated from OpenAI.
- CV uses the vendor PDF's individual C and O element-map panels extracted from page 2, with N stored as a relative comparison feature.
- Residue Review shows only the two decision images: composite EDS map and Element Maps (C/N/O/Si).
- OpenAI is moved to a separate `AI Analysis` page for experiment-level interpretation, trends, anomalies, caveats, and next-step suggestions.
- Image Gallery displays the actual stored EDS images and opens a lightbox instead of routing to AI Review.
- Human Review remains the place to override CV results.

## Important scientific note
The CV signal uses relative pixel/map intensity from EDS elemental maps. It is not treated as direct elemental concentration. Quantitative wt%/at% from the vendor spectrum/table remains a separate measurement source.

## Validation
- Python backend syntax checked.
- Point worker tested on the current Bruker PDF structure (3 pages / point).
- Extracted assets verified: SEM, composite EDS, Element Maps, C/N/O/Si maps, source pages.
- Next.js source passed `node --check`. Full local Next build could not be completed in this environment because dependency installation timed out.
