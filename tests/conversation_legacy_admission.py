from fastapi import FastAPI
from fastapi.routing import APIRoute
from starlette.routing import Match
from starlette.types import ASGIApp, Receive, Scope, Send

from app.routers.api_project_library import append_project_conversation_message


def with_legacy_conversation_admission(app: ASGIApp) -> ASGIApp:
    # Exercise the removed POST contract without altering the public router or GET recovery.
    route = APIRoute(
        "/api/project-library/{project_id}/conversation/{node_id}/messages",
        append_project_conversation_message,
        methods=["POST"],
    )
    legacy = FastAPI(openapi_url=None)
    legacy.router.routes.append(route)

    async def dispatch(scope: Scope, receive: Receive, send: Send) -> None:
        target = legacy if scope["type"] == "http" and route.matches(scope)[0] is Match.FULL else app
        await target(scope, receive, send)

    return dispatch
