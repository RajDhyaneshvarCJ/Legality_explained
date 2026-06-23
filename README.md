# legal document explainer

upload or paste a legal document, get a plain-english breakdown: summary, clause explanations, red flags, party obligations, glossary, plus a chat to ask follow-up questions.

## run it

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add your api key
python main.py         # http://localhost:8000
```

## switching providers

the llm provider is entirely env-driven. no code changes needed.

groq (default, free tier):

```
LLM_API_KEY=gsk_...
```

anthropic (production):

```
LLM_API_KEY=sk-ant-...
LLM_BASE_URL=https://api.anthropic.com/v1/
LLM_MODEL=claude-sonnet-4-20250514
```

this works because anthropic exposes an openai-compatible endpoint, so the same openai sdk client talks to both. note the compatibility layer skips some claude-only features (prompt caching, structured outputs) — if you want those later, swap to the native anthropic sdk. docs: https://docs.claude.com/en/api/openai-sdk

## password gate

set `APP_PASSWORD` to require a password before the app is usable — useful for sharing a demo link without opening it to anyone who finds the url.

```
APP_PASSWORD=your-chosen-password
COOKIE_SECRET=any-random-string
```

if `APP_PASSWORD` is unset, auth is off entirely (default for local dev). when set:
- `/` serves a login page until a valid session cookie is present
- `/analyze` and `/chat` return 401 without one — the gate is enforced server-side, not just hidden in the frontend, so it actually protects your llm quota
- the session cookie is signed (hmac-sha256) and lasts 7 days
- set `COOKIE_SECURE=false` only if testing over plain http; leave `true` in production so the cookie requires https



```
browser (static/index.html, vanilla js)
   |
   v
fastapi (main.py)
   |-- /analyze  -> extractor (pdf/docx/txt) -> chunker -> analyzer (llm) -> json
   |-- /chat     -> analyzer chat (analysis + raw text as context)
```

- documents over ~20k chars are split at paragraph/sentence boundaries, analyzed in parallel (4 workers), then merged by a final llm pass
- llm calls retry up to 3 times with exponential backoff on rate limits and connection errors
- invalid json from the model gets one repair pass before giving up
- blocking llm work runs in a threadpool so the server stays responsive

## limits

- 10mb file max, 400k character max
- scanned pdfs (no text layer) are rejected — ocr is out of scope for v1
- legacy .doc not supported — save as .docx first
- no auth, no persistence — every analysis is stateless
