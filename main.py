import os
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

from services.extractor import extract_text, ExtractionError
from services.analyzer import analyze_document, chat_with_document, MODEL, AnalysisError
from services import auth

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 400_000
MAX_CHAT_MESSAGE_CHARS = 4_000
MAX_CHAT_HISTORY_TURNS = 20

app = FastAPI(title="legal explainer")

# open cors is fine for local dev. for production, set ALLOWED_ORIGINS
# to your real domain, e.g. ALLOWED_ORIGINS=https://yourapp.com
allowed_origins = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def root(request: Request):
    if auth.APP_PASSWORD and not auth.is_valid_session_cookie(request.cookies.get(auth.COOKIE_NAME)):
        return FileResponse("static/login.html")
    return FileResponse("static/index.html")


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL}


class LoginRequest(BaseModel):
    password: str


@app.post("/login")
def login(req: LoginRequest):
    if not auth.check_password(req.password):
        raise HTTPException(status_code=401, detail="incorrect password")
    response = JSONResponse({"ok": True})
    response.set_cookie(
        auth.COOKIE_NAME,
        auth.make_session_cookie(),
        max_age=auth.SESSION_SECONDS,
        httponly=True,
        samesite="lax",
        secure=os.getenv("COOKIE_SECURE", "true").lower() == "true",
    )
    return response


@app.post("/logout")
def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE_NAME)
    return response


@app.post("/analyze", dependencies=[Depends(auth.require_auth)])
async def analyze(
    file: UploadFile = File(None),
    text: str = Form(None),
    doc_type: str = Form("auto")
):
    if not file and not text:
        raise HTTPException(status_code=400, detail="provide a file or text")

    if file:
        contents = await file.read()
        if len(contents) > MAX_FILE_BYTES:
            raise HTTPException(status_code=400, detail="file too large - 10mb max")
        try:
            extracted = await run_in_threadpool(extract_text, contents, file.filename)
        except ExtractionError as e:
            raise HTTPException(status_code=422, detail=str(e))
    else:
        extracted = text.strip()

    if not extracted:
        raise HTTPException(status_code=400, detail="could not extract any text from the document")

    if len(extracted) > MAX_TEXT_CHARS:
        raise HTTPException(status_code=413, detail="document too long - 400k character max")

    try:
        # run_in_threadpool keeps the event loop free while the llm calls run
        result = await run_in_threadpool(analyze_document, extracted, doc_type)
    except AnalysisError as e:
        raise HTTPException(status_code=502, detail=str(e))

    # source_text lets the chat endpoint answer questions about details
    # that didn't make it into the structured analysis
    result["source_text"] = extracted[:30_000]
    return result


class ChatRequest(BaseModel):
    message: str = Field(max_length=MAX_CHAT_MESSAGE_CHARS)
    analysis: str = ""
    source_text: str = ""
    history: list = []


@app.post("/chat", dependencies=[Depends(auth.require_auth)])
async def chat(req: ChatRequest):
    history = req.history[-MAX_CHAT_HISTORY_TURNS:]
    try:
        response = await run_in_threadpool(
            chat_with_document, req.message, req.analysis, req.source_text, history
        )
    except AnalysisError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"response": response}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
