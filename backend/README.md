# Backend

## Local run

Python 3.11+ recommended.

```bash
cd backend
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8000
```

Set `OPENAI_API_KEY` in `.env` to enable the real OpenAI analysis endpoint.

The code uses the official OpenAI Python SDK and the Responses API. Images are sent as base64 data URLs and the response is parsed into a Pydantic schema.

Important: keep the API key only on the backend. Never expose it as `NEXT_PUBLIC_OPENAI_API_KEY`.
