import asyncio
import json
import logging
import os
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse

from agentic_pipeline import run_agentic_question

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.getenv("DUCKDB_PATH", os.path.join(BASE_DIR, "deposit_real.duckdb"))

app = FastAPI(title="Qwen Semantic Query WebSocket")
trace_logger = logging.getLogger("query_trace")
trace_logger.setLevel(logging.INFO)
if not trace_logger.handlers:
    trace_handler = logging.StreamHandler()
    trace_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S"))
    trace_logger.addHandler(trace_handler)
trace_logger.propagate = False


def trace(request_id: str, phase: str, message: str) -> None:
    trace_logger.info("request=%s phase=%s %s", request_id, phase, message)


@app.get("/")
async def home() -> FileResponse:
    return FileResponse(os.path.join(BASE_DIR, "web", "index.html"))


def run_question(question: str, request_id: str) -> dict:
    return run_agentic_question(question, request_id)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "llm": "qwen",
        "qwen_configured": bool(os.getenv("QWEN_OPENAI_API_KEY") or os.getenv("QWEN_API_KEY")),
        "database_available": os.path.isfile(DATABASE_PATH),
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    connection_id = uuid.uuid4().hex[:8]
    trace(connection_id, "websocket", "connected")
    try:
        while True:
            raw_message = await websocket.receive_text()
            request_id = uuid.uuid4().hex[:8]
            trace(request_id, "request", f"received websocket_message_bytes={len(raw_message.encode('utf-8'))}")
            try:
                message = json.loads(raw_message)
            except json.JSONDecodeError:
                message = raw_message

            question = message.get("question") if isinstance(message, dict) else message
            if not isinstance(question, str) or not question.strip():
                trace(request_id, "request.validate", "rejected empty or invalid question")
                await websocket.send_json({
                    "request_id": request_id,
                    "type": "error",
                    "code": "invalid_request",
                    "message": "Send a question as text or as {\"question\": \"...\"}.",
                })
                continue

            try:
                trace(request_id, "request.validate", f"accepted question_chars={len(question.strip())}")
                result = await asyncio.to_thread(run_question, question.strip(), request_id)
                if result.get("type") == "error":
                    result["request_id"] = request_id
                await websocket.send_json(jsonable_encoder(result))
                trace(request_id, "websocket.send", "response sent")
            except Exception as error:
                trace(request_id, "request", f"failed error={type(error).__name__}: {str(error)[:300]}")
                await websocket.send_json({
                    "request_id": request_id,
                    "type": "error",
                    "code": "query_failed",
                    "message": str(error),
                })
    except WebSocketDisconnect:
        trace(connection_id, "websocket", "disconnected")
        return