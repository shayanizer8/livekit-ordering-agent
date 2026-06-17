"""
token_server.py
---------------
Lightweight aiohttp HTTP server that generates LiveKit access tokens for the
frontend.  A single GET /token endpoint accepts optional `identity` and `room`
query parameters and returns a JSON object containing the signed JWT and the
LiveKit WebSocket URL.

Run:
    python backend/token_server.py
"""

import datetime
import json
import logging
import os
import sys

from aiohttp import web
from dotenv import load_dotenv
from livekit.api import (
    AccessToken,
    RoomAgentDispatch,
    RoomConfiguration,
    VideoGrants,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

# Load .env that lives next to this file (backend/.env)
_ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(dotenv_path=_ENV_PATH)

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")
LIVEKIT_AGENT_NAME = os.getenv("LIVEKIT_AGENT_NAME", "forge-flame-agent")

# Validate required environment variables at startup so the problem is obvious.
_MISSING = [
    name
    for name, value in {
        "LIVEKIT_URL": LIVEKIT_URL,
        "LIVEKIT_API_KEY": LIVEKIT_API_KEY,
        "LIVEKIT_API_SECRET": LIVEKIT_API_SECRET,
    }.items()
    if not value
]
if _MISSING:
    logger.error("Missing required environment variables: %s", ", ".join(_MISSING))
    sys.exit(1)

# ---------------------------------------------------------------------------
# CORS helper
# ---------------------------------------------------------------------------

_CORS_ORIGIN = "http://localhost:5173"

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": _CORS_ORIGIN,
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


def _json_response(data: dict, status: int = 200) -> web.Response:
    """Return a JSON response with CORS headers applied."""
    return web.Response(
        status=status,
        content_type="application/json",
        headers=_CORS_HEADERS,
        text=json.dumps(data),
    )


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


async def handle_options(request: web.Request) -> web.Response:  # noqa: ARG001
    """Handle CORS pre-flight OPTIONS requests."""
    return web.Response(status=204, headers=_CORS_HEADERS)


async def handle_token(request: web.Request) -> web.Response:
    """
    GET /token

    Query parameters:
        identity  – participant identity   (default: "user")
        room      – LiveKit room name      (default: "ordering-room")

    Returns:
        200  { "token": "<jwt>", "url": "<wss://...>" }
        500  { "error": "<message>" }
    """
    identity: str = request.rel_url.query.get("identity", "user") or "user"
    room: str = request.rel_url.query.get("room", "ordering-room") or "ordering-room"

    logger.info("Token request  identity=%r  room=%r", identity, room)

    try:
        grants = VideoGrants(
            room_join=True,
            room=room,
        )

        token: str = (
            AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
            .with_identity(identity)
            .with_name(identity)
            .with_grants(grants)
            .with_room_config(
                RoomConfiguration(
                    agents=[RoomAgentDispatch(agent_name=LIVEKIT_AGENT_NAME)],
                )
            )
            .with_ttl(datetime.timedelta(hours=2))
            .to_jwt()
        )

        logger.info("Token generated successfully for identity=%r room=%r", identity, room)

        return _json_response({"token": token, "url": LIVEKIT_URL})

    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Failed to generate token: %s", exc)
        return _json_response({"error": f"Token generation failed: {exc}"}, status=500)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app() -> web.Application:
    app = web.Application()

    # OPTIONS pre-flight for /token
    app.router.add_route("OPTIONS", "/token", handle_options)

    # Main token endpoint
    app.router.add_get("/token", handle_token)

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    HOST = "localhost"
    PORT = 8080

    app = create_app()

    logger.info("Token server starting on http://%s:%d", HOST, PORT)
    logger.info("  GET http://%s:%d/token", HOST, PORT)

    web.run_app(app, host=HOST, port=PORT, print=None)
