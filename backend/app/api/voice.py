"""Voice agent endpoints: relay the WebRTC session to the voice platform and
execute the knowledge-base tools the agent calls.

The browser talks audio to OpenAI directly (via the SDP answer we relay) and
sends each tool call here. Platform credentials stay server-side.
"""
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth import require_token
from app.config import get_settings
from app.db.neo4j_client import get_driver
from app.llm.client import LLMError
from app.services.voice_client import VoicePlatformError, VoiceClient
from app.services.voice_tools import TOOL_DEFINITIONS, ToolError, build_instructions, run_tool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/voice", tags=["voice"])

MAX_SDP_BYTES = 64 * 1024


class ToolCall(BaseModel):
    arguments: dict = Field(default_factory=dict)


class TranscriptBody(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    transcript: str = Field(max_length=200_000)
    duration_seconds: int = Field(0, ge=0, le=86_400)


@router.get("/status")
async def voice_status():
    return {"configured": get_settings().voice_configured}


@router.post("/session")
async def create_session(request: Request, token: str = Depends(require_token)):
    """Body: the browser's raw SDP offer (Content-Type: application/sdp)."""
    raw = await request.body()
    if not raw or len(raw) > MAX_SDP_BYTES:
        raise HTTPException(status_code=400, detail="SDP offer required (max 64 KiB)")
    started = time.monotonic()
    try:
        client = VoiceClient(get_settings())
        platform = await client.create_webrtc_session(
            token, raw.decode("utf-8", errors="replace")
        )
    except VoicePlatformError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))

    driver = get_driver()
    async with driver.session() as session:
        instructions = await build_instructions(session)

    logger.info("voice session created in %.2fs", time.monotonic() - started)
    return {
        "sdp": platform["sdp"],
        "agent": platform.get("agent", {}),
        # Replace the platform agent's own tools/prompt with the KB-grounded ones;
        # the browser applies these with a session.update on the data channel.
        "instructions": instructions,
        "tools": TOOL_DEFINITIONS,
    }


@router.post("/tools/{name}")
async def call_tool(name: str, body: ToolCall):
    driver = get_driver()
    async with driver.session() as session:
        try:
            return await run_tool(session, name, body.arguments)
        except ToolError as e:
            # Returned (not raised) so the agent can say what went wrong.
            return {"output": {"error": str(e)}}
        except LLMError as e:
            raise HTTPException(status_code=502, detail=str(e))


@router.post("/transcript")
async def save_transcript(body: TranscriptBody, token: str = Depends(require_token)):
    if not body.transcript.strip():
        return {"saved": False}
    try:
        await VoiceClient(get_settings()).save_transcript(
            token, body.session_id, body.transcript, body.duration_seconds
        )
    except VoicePlatformError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e))
    return {"saved": True}
