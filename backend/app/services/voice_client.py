"""Client for the voice agent platform's REST API (gluer-platform).

Every call acts as the signed-in user: the caller passes the user's platform
bearer token through, so the platform applies its own per-user rules (agent
access, OpenAI key lookup). Vigory holds no platform credentials.
"""
import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class VoicePlatformError(Exception):
    """The voice platform is unconfigured, unreachable, or rejected a call."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class VoiceClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        if not settings.voice_configured:
            raise VoicePlatformError(
                "Voice agent is not configured (set VOICE_API_URL and VOICE_AGENT_ID)",
                status_code=503,
            )
        self._settings = settings
        self._base = settings.voice_api_url.rstrip("/")
        self._transport = transport

    async def _post(self, token: str, path: str, **kwargs: Any) -> dict[str, Any]:
        params = {}
        if self._settings.voice_workspace_id:
            params["workspace_id"] = self._settings.voice_workspace_id
        headers = {**kwargs.pop("headers", {}), "Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(
            base_url=self._base, timeout=30.0, transport=self._transport
        ) as client:
            try:
                res = await client.post(path, params=params, headers=headers, **kwargs)
            except httpx.RequestError as e:
                raise VoicePlatformError(f"Cannot reach voice platform: {e}") from e
        if res.status_code == 401:
            raise VoicePlatformError("Voice platform session expired; sign in again", 401)
        if not res.is_success:
            try:
                detail = res.json().get("detail", res.text)
            except ValueError:
                detail = res.text
            logger.warning("voice platform returned %s: %s", res.status_code, detail)
            raise VoicePlatformError(f"Voice platform error ({res.status_code}): {detail}")
        return res.json()

    async def create_webrtc_session(self, token: str, sdp_offer: str) -> dict[str, Any]:
        """Relay the browser's SDP offer; returns {sdp, tools, agent, ...}."""
        return await self._post(
            token,
            f"/api/v1/realtime/session/{self._settings.voice_agent_id}",
            content=sdp_offer.encode(),
            headers={"Content-Type": "application/sdp"},
        )

    async def save_transcript(
        self, token: str, session_id: str, transcript: str, duration: int
    ) -> None:
        await self._post(
            token,
            f"/api/v1/realtime/transcript/{self._settings.voice_agent_id}",
            json={"session_id": session_id, "transcript": transcript, "duration_seconds": duration},
        )
