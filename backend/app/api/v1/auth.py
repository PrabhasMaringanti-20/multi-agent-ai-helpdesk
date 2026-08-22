"""Authentication routes: register, login, refresh, logout, current user.

Route (POST /chat is shorthand elsewhere; here paths are literal):
    POST /auth/register  -> 201 UserResponse
    POST /auth/login     -> 200 TokenResponse
    POST /auth/refresh   -> 200 TokenResponse
    POST /auth/logout    -> 200 MessageResponse   (requires access token)
    GET  /auth/me        -> 200 UserResponse       (requires access token)
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from app.api.deps import (
    AuthServiceDep,
    CurrentPrincipal,
    CurrentUser,
    client_ip,
)
from app.core.config import get_settings
from app.core.security import IssuedToken
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.schemas.common import MessageResponse

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_response(access: IssuedToken, refresh: IssuedToken) -> TokenResponse:
    settings = get_settings()
    return TokenResponse(
        access_token=access.token,
        refresh_token=refresh.token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new end-user within an organization",
)
async def register(payload: RegisterRequest, service: AuthServiceDep) -> UserResponse:
    user = await service.register(payload)
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenResponse, summary="Authenticate and issue tokens")
async def login(payload: LoginRequest, request: Request, service: AuthServiceDep) -> TokenResponse:
    user = await service.authenticate(payload.org_slug, payload.email, payload.password)
    access, refresh = await service.issue_token_pair(
        user,
        user_agent=request.headers.get("user-agent"),
        ip_address=client_ip(request),
    )
    return _token_response(access, refresh)


@router.post("/refresh", response_model=TokenResponse, summary="Rotate the refresh token")
async def refresh(
    payload: RefreshRequest, request: Request, service: AuthServiceDep
) -> TokenResponse:
    access, refresh_token = await service.refresh_tokens(
        payload.refresh_token,
        user_agent=request.headers.get("user-agent"),
        ip_address=client_ip(request),
    )
    return _token_response(access, refresh_token)


@router.post("/logout", response_model=MessageResponse, summary="Revoke a refresh session")
async def logout(
    payload: LogoutRequest,
    principal: CurrentPrincipal,
    service: AuthServiceDep,
) -> MessageResponse:
    await service.logout(payload.refresh_token, actor_id=principal.user_id, org_id=principal.org_id)
    return MessageResponse(detail="Logged out successfully.")


@router.get("/me", response_model=UserResponse, summary="Return the current authenticated user")
async def me(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)


__all__ = ["router"]
