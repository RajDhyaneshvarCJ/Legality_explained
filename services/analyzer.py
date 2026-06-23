import os
import json
import time
import logging
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI, APIStatusError, APIConnectionError, RateLimitError
from services.chunker import chunk_text

logger = logging.getLogger("analyzer")
#check readme for anthropic setup

BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
API_KEY = os.getenv("LLM_API_KEY") or os.getenv("GROQ_API_KEY")
MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

MAX_RETRIES = 3
CHUNK_WORKERS = 4


class AnalysisError(Exception):
    pass


ANALYSIS_SYSTEM_PROMPT = """
you are a legal document explainer. your job is to help non-lawyers understand what a document says.
you do not give legal advice. you explain clearly and flag anything the reader should pay close attention to.

rules for your output:
- plain_english fields must be written like you are explaining to a smart friend with no legal background
- avoid legal jargon in explanations. if a legal term is unavoidable, explain it in brackets immediately after
- summaries should be conversational and direct, not formal
- red flags must be specific — name the actual risk, not just the clause
- obligations must be concrete actions, not vague statements like "comply with laws"
- for glossary terms: pick words a non-lawyer would find confusing, not obvious ones

return ONLY valid json in this exact structure, no preamble, no markdown fences:
{
  "summary": "3-5 sentence plain english tldr. start with what this agreement is fundamentally about, then who benefits, then what to watch out for at a high level",
  "document_type": "detected type e.g. nda, lease, employment contract, terms of service",
  "party_a_name": "actual name of party a if found, otherwise null",
  "party_b_name": "actual name of party b if found, otherwise null",
  "clauses": [
    {
      "title": "short clause name",
      "plain_english": "2-3 sentences explaining what this clause actually means for the people signing it. be specific about numbers, dates, and consequences where they exist"
    }
  ],
  "red_flags": [
    {
      "clause": "article or section name",
      "concern": "specific explanation of why this is risky and what could actually happen to the reader as a result"
    }
  ],
  "obligations": {
    "party_a": ["concrete action with specifics e.g. pay USD 52,000,000 by Q1 2025, not just contribute capital"],
    "party_b": ["same level of specificity"]
  },
  "glossary": [
    {
      "term": "legal or technical term from the document",
      "plain_english": "one sentence explanation a non-lawyer would understand"
    }
  ]
}

if the document is too short or unclear to fill a section, return an empty array for that section.
never invent information not in the document.
"""

CHUNK_SYSTEM_PROMPT = """
you are analyzing a section of a legal document. extract all relevant information from this section only.
return ONLY valid json with no preamble or markdown fences:
{
  "party_a_name": "actual name if found, otherwise null",
  "party_b_name": "actual name if found, otherwise null",
  "clauses": [
    {
      "title": "short clause name",
      "plain_english": "2-3 sentences explaining what this clause means for the people signing. be specific about numbers, dates, consequences"
    }
  ],
  "red_flags": [
    {
      "clause": "article or section name",
      "concern": "specific explanation of the actual risk and what could happen as a result"
    }
  ],
  "obligations": {
    "party_a": ["concrete action with specifics"],
    "party_b": ["concrete action with specifics"],
    "party_c": ["concrete action with specifics"],
    "party_d": ["concrete action with specifics"]
  },
  "glossary": [
    {
      "term": "legal or technical term",
      "plain_english": "one sentence a non-lawyer would understand"
    }
  ]
}
"""

MERGE_SYSTEM_PROMPT = """
you are merging multiple partial analyses of different sections of the same legal document into one clean, unified analysis.
remove duplicates. reconcile any conflicts by preferring the more specific or detailed version.
the summary and document_type must cover the whole document, not just one section.
obligations must include all parties found across all sections — not just party_a and party_b.
glossary must include the most useful terms across all sections — deduplicate and keep the clearest explanations.

return ONLY valid json with no preamble or markdown fences:
{
  "summary": "3-5 sentence plain english tldr of the whole document",
  "document_type": "detected type",
  "party_a_name": "actual name or null",
  "party_b_name": "actual name or null",
  "clauses": [
    { "title": "clause name", "plain_english": "explanation" }
  ],
  "red_flags": [
    { "clause": "section name", "concern": "specific risk" }
  ],
  "obligations": {
    "party_a": ["..."],
    "party_b": ["..."],
    "party_c": ["..."],
    "party_d": ["..."]
  },
  "glossary": [
    { "term": "term", "plain_english": "explanation" }
  ]
}
"""


def _chat(messages: list, max_tokens: int, temperature: float = 0.2) -> str:
    # retries cover rate limits (groq free tier hits these) and transient
    # network errors, with exponential backoff: 1s, 2s, 4s
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens
            )
            return response.choices[0].message.content.strip()
        except (RateLimitError, APIConnectionError) as e:
            last_err = e
            wait = 2 ** attempt
            logger.warning("llm call failed (%s), retrying in %ss", type(e).__name__, wait)
            time.sleep(wait)
        except APIStatusError as e:
            raise AnalysisError(f"llm provider returned an error ({e.status_code}) - try again shortly")
    raise AnalysisError(f"llm provider unreachable after {MAX_RETRIES} attempts: {last_err}")


def _strip_fences(raw: str) -> str:
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


def _call_model_json(system: str, user: str, max_tokens: int = 2048) -> dict:
    raw = _chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens
    )
    raw = _strip_fences(raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # one repair pass: ask the model to fix its own broken json instead of
    # silently returning an empty analysis
    logger.warning("model returned invalid json, attempting repair")
    fixed = _chat(
        [
            {"role": "system", "content": "fix this so it is valid json. return ONLY the corrected json, nothing else."},
            {"role": "user", "content": raw}
        ],
        max_tokens
    )
    try:
        return json.loads(_strip_fences(fixed))
    except json.JSONDecodeError:
        logger.error("json repair failed, returning empty result")
        return {}


def analyze_document(text: str, doc_type: str = "auto") -> dict:
    chunks = chunk_text(text)

    if len(chunks) == 1:
        user_prompt = f"document type hint: {doc_type}\n\ndocument text:\n{chunks[0]}"
        result = _call_model_json(ANALYSIS_SYSTEM_PROMPT, user_prompt, max_tokens=3000)
        return _ensure_structure(result)

    # analyze chunks in parallel — serial analysis made long documents
    # painfully slow (n chunks = n sequential round trips)
    def analyze_chunk(args):
        i, chunk = args
        user_prompt = f"document section {i + 1} of {len(chunks)}:\n\n{chunk}"
        return _call_model_json(CHUNK_SYSTEM_PROMPT, user_prompt, max_tokens=2500)

    with ThreadPoolExecutor(max_workers=CHUNK_WORKERS) as pool:
        partial_results = [p for p in pool.map(analyze_chunk, enumerate(chunks)) if p]

    if not partial_results:
        return _empty_structure()

    merge_input = f"document type hint: {doc_type}\n\npartial analyses to merge:\n{json.dumps(partial_results, indent=2)}"
    merged = _call_model_json(MERGE_SYSTEM_PROMPT, merge_input, max_tokens=3500)
    return _ensure_structure(merged)


def _ensure_structure(data: dict) -> dict:
    # guarantees the frontend never crashes on missing keys
    obligations = data.get("obligations") or {}
    return {
        "summary": data.get("summary", "could not generate summary"),
        "document_type": data.get("document_type", "unknown"),
        "party_a_name": data.get("party_a_name"),
        "party_b_name": data.get("party_b_name"),
        "clauses": data.get("clauses") or [],
        "red_flags": data.get("red_flags") or [],
        "obligations": {
            "party_a": obligations.get("party_a") or [],
            "party_b": obligations.get("party_b") or [],
            "party_c": obligations.get("party_c") or [],
            "party_d": obligations.get("party_d") or [],
        },
        "glossary": data.get("glossary") or []
    }


def _empty_structure() -> dict:
    return _ensure_structure({})


def chat_with_document(message: str, analysis: str, source_text: str, history: list) -> str:
    # the chat gets both the structured analysis (for quick answers) and the
    # raw document text (for details the analysis didn't capture)
    system = f"""you are a helpful assistant answering questions about a legal document.
use the structured analysis for quick answers and the original document text to verify details or answer questions the analysis does not cover.
answer clearly in plain english. do not give legal advice.
if something is not in the analysis or the document text, say so honestly.

structured analysis:
{analysis[:8000]}

original document text (may be truncated):
{source_text[:20000]}
"""
    messages = [{"role": "system", "content": system}]
    for turn in history:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": str(turn["content"])})
    messages.append({"role": "user", "content": message})

    return _chat(messages, max_tokens=1024, temperature=0.3)
