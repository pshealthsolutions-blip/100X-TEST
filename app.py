"""Stateless Vercel entry point; reuses the original PS SMS learning engine."""
import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from groq import Groq
from pydantic import BaseModel, Field
from sms_learning import WELCOME, initial_state, teach

app = FastAPI(title="Learning Assistant Chatbot", docs_url=None, redoc_url=None)


def client():
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        raise HTTPException(503, "GROQ_API_KEY is not configured in Vercel.")
    return Groq(api_key=key, timeout=45, max_retries=0)


def signing_key():
    key = os.environ.get("SESSION_SECRET") or os.environ.get("GROQ_API_KEY")
    if not key:
        raise HTTPException(503, "Server configuration is incomplete.")
    return hashlib.sha256(("learning-session:" + key).encode()).digest()


def pack(state):
    payload = base64.urlsafe_b64encode(json.dumps({"state": state, "expires": int(time.time()) + 86400}, separators=(",", ":")).encode()).decode()
    signature = hmac.new(signing_key(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + "." + signature


def unpack(token):
    if not token:
        return initial_state()
    try:
        payload, signature = token.rsplit(".", 1)
        expected = hmac.new(signing_key(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        data = json.loads(base64.urlsafe_b64decode(payload))
        if data["expires"] < time.time():
            raise ValueError()
        return data["state"]
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, "Session expired or invalid. Select Restart to begin again.") from None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=6000)
    token: str = Field(default="", max_length=250000)


@app.get("/", response_class=HTMLResponse)
def home():
    return Path(__file__).with_name("web.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health():
    return {"status": "ok", "configured": bool(os.environ.get("GROQ_API_KEY", "").strip())}


@app.get("/api/welcome")
def welcome():
    return {"reply": WELCOME}


@app.post("/api/chat")
def chat(request: ChatRequest):
    message = request.message.strip()
    if not message:
        raise HTTPException(422, "Please enter a message.")
    state = unpack(request.token)
    if len(state["attempts"]) >= 100 and message.lower() not in ("topics", "restart", "start over", "menu"):
        raise HTTPException(400, "Session limit reached. Select Restart.")
    try:
        reply, updated = teach(client(), message, [], state)
        return {"reply": reply, "token": pack(updated)}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, "The answer could not be checked. Retry; your progress has not changed.") from None


@app.post("/api/transcribe")
def transcribe(file: UploadFile):
    content = file.file.read(3_000_001)
    if not content or len(content) > 3_000_000:
        raise HTTPException(413, "Record a shorter clip (maximum 3 MB).")
    allowed = {"audio/webm", "audio/mp4", "audio/wav", "audio/mpeg", "audio/ogg", "video/webm"}
    mime = (file.content_type or "").split(";")[0]
    if mime not in allowed:
        raise HTTPException(415, "Unsupported audio format.")
    extension = {"audio/mp4": "m4a", "audio/wav": "wav", "audio/mpeg": "mp3", "audio/ogg": "ogg"}.get(mime, "webm")
    try:
        result = client().audio.transcriptions.create(file=("recording." + extension, content, mime), model="whisper-large-v3-turbo", language="en")
        return {"text": result.text.strip()}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, "Dictation failed. Your typed draft has been kept; please retry.") from None
