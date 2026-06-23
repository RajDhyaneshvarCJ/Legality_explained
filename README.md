# Legality Explained

A legal document explainer that converts contracts, leases, and agreements into plain English.

Upload a PDF or DOCX, or paste text directly. Get back a summary, clause breakdown, red flags, party obligations, and a glossary. Ask follow-up questions via the built-in chat.

## Stack

- Backend: FastAPI + Python
- LLM: Groq (Llama 3.3 70B) via OpenAI-compatible API
- Frontend: Vanilla JS, single HTML file
- Deployment: Railway

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py
```

## Environment Variables
GROQ_API_KEY=

APP_PASSWORD=

COOKIE_SECRET=

COOKIE_SECURE=false

ALLOWED_ORIGINS=*

## Deploy

Push to GitHub, connect to Railway, add the environment variables, and set the start command:
uvicorn main:app --host 0.0.0.0 --port $PORT

## notes

- Supports PDF, DOCX, and TXT up to 10MB
- Password protected via signed session cookie
- Not a substitute for legal advice