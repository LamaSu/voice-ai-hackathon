"""FastAPI server: SmallWebRTC signaling (/api/offer) + built frontend.

Run: uv run python -m app.server
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import BackgroundTasks, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

from app.bot import SharedResources, run_session
from app.config import REPO_DIR, get_settings

shared: SharedResources | None = None
handler = SmallWebRTCRequestHandler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global shared
    shared = SharedResources()
    if shared.settings.enable_speaker_id:
        # warm the ECAPA model so the first session has speaker ID immediately
        asyncio.get_running_loop().run_in_executor(None, shared.embedder)
    jev = shared.new_jev()
    try:
        await jev.warmup()
        logger.info("Jev ready" if shared.settings.jev_api_key else "Jev disabled (no key)")
    finally:
        await jev.aclose()
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
async def health():
    s = get_settings()
    return {
        "ok": True,
        "llm_model": s.llm_model,
        "stt": "gradium",
        "jev_model": s.jev_model if s.jev_api_key else None,
        "speaker_id": s.enable_speaker_id,
    }


@app.get("/api/memory")
async def memory():
    return shared.memory.to_ui() if shared else {}


@app.post("/api/memory/reset")
async def reset_memory():
    """Forget everyone (names, voice profiles, facts, summary) — works with or without a call."""
    return await shared.reset_all() if shared else {}


@app.post("/api/offer")
async def offer(request: SmallWebRTCRequest, background_tasks: BackgroundTasks):
    async def on_connection(connection: SmallWebRTCConnection):
        transport = SmallWebRTCTransport(
            webrtc_connection=connection,
            params=TransportParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                audio_in_sample_rate=16000,
            ),
        )
        background_tasks.add_task(run_session, transport, shared, connection.pc_id[-8:])

    answer = await handler.handle_web_request(request, on_connection)
    return JSONResponse(answer)


@app.patch("/api/offer")
async def ice(request: SmallWebRTCPatchRequest):
    await handler.handle_patch_request(request)
    return {"status": "success"}


if os.getenv("DEV_FIXTURES") == "1":
    # Dev only: lets a browser test harness play the Gradium-voiced test clips as a fake mic.
    from fastapi.responses import FileResponse

    @app.get("/api/dev/fixture/{name}.wav")
    async def fixture(name: str):
        path = (REPO_DIR / "backend" / "tests" / "fixtures" / f"{name}.wav").resolve()
        if not path.is_file() or path.parent.name != "fixtures":
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(path, media_type="audio/wav")


_dist = REPO_DIR / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")


def main() -> None:
    s = get_settings()
    uvicorn.run(app, host=s.host, port=s.port)


if __name__ == "__main__":
    main()
