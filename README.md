# UV Tape Residue EDS Full System

This is the full-system demo architecture, not a UI-only mock.

## Included

### Frontend
- Next.js dashboard
- upload / project flow
- AI review queue
- human-in-the-loop override
- image gallery
- condition comparison
- point detail
- report preview
- CSV/PDF/PPT export controls

### Backend
- FastAPI
- PDF page parsing with PyMuPDF
- page identity extraction: Power / Time / Wafer / Point
- standard 1-page report crop extraction
- SEM candidate ROI detection with OpenCV
- C/O enrichment and coverage features
- residue score and confidence
- OpenAI Responses API endpoint
- structured AI result schema
- server-side API key handling
- PDF/PPT report generation

## Run

Terminal 1:
```bash
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8000
```

Terminal 2:
```bash
cd frontend
npm install
copy .env.example .env.local
npm run dev
```

Open http://localhost:3000

## OpenAI
Put the key only in `backend/.env`:
`OPENAI_API_KEY=...`

The frontend never receives the key. The backend calls the official OpenAI Python SDK Responses API.

## Real-data limitations
The PDF crop coordinates are based on the current standardized report layout supplied by the user. If the EDS vendor export has a different layout, the crop/parser should be made vendor-specific. Element-map quantitative values should not be inferred from raw RGB brightness alone; quantitative EDS values should come from the vendor's numerical output where available.
