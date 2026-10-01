from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth import (
    check_throttle,
    clear_failures,
    platform_login,
    record_failure,
)
from app.config import get_settings

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


@router.get("/status")
async def auth_status():
    return {"enabled": get_settings().auth_enabled}


@router.post("/login")
async def login(body: LoginBody, request: Request):
    settings = get_settings()
    if not settings.auth_enabled:
        raise HTTPException(status_code=400, detail="Authentication is not enabled")
    client = request.client.host if request.client else "unknown"
    check_throttle(client)
    try:
        token = await platform_login(settings, body.email, body.password)
    except HTTPException as e:
        if e.status_code == 401:
            record_failure(client)
        raise
    clear_failures(client)
    return {"token": token}
