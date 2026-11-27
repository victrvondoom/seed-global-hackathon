from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Header, Request

from app.core.errors import NotFound, Unauthorized
from app.core.security import decode_access_token
from app.models import Trip, User
from app.services.runtime import Ctx, Runtime


def get_rt(request: Request) -> Runtime:
    return request.app.state.rt


def get_ctx(rt: Runtime = Depends(get_rt)) -> Iterator[Ctx]:
    s = rt.db.SessionLocal()
    try:
        yield Ctx(rt=rt, db=s)
    finally:
        s.close()


def current_user(ctx: Ctx = Depends(get_ctx), authorization: str | None = Header(default=None)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("auth.required", "Sign in to continue.")
    uid = decode_access_token(authorization[7:].strip(), ctx.settings)
    user = ctx.db.get(User, uid)
    if not user:
        raise Unauthorized("auth.unknown_user", "Sign in to continue.")
    ctx.user_id = user.id
    return user


def owned_trip(trip_id: str, ctx: Ctx = Depends(get_ctx), user: User = Depends(current_user)) -> Trip:
    t = ctx.db.get(Trip, trip_id)
    if not t or t.owner_id != user.id:  # same answer for missing and foreign: no existence leak
        raise NotFound("trip.not_found", "Trip not found")
    return t
