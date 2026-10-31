"""
CloudOps console server (FastAPI): sign-in, the JSON API, and the React app.

    uvicorn console.server:app --port 8080            # web/dist must be built (npm run build)
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

import config
from console import api, auth
from src.core.monitoring import configure_logging

configure_logging("console")

STATIC = next((p for p in (Path(__file__).parent / "static", Path(__file__).parent.parent / "web" / "dist")
               if (p / "index.html").exists()), None)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

app = FastAPI(title="CloudOps console", docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    # State-changing API calls must carry a custom header. Browsers only send it
    # from our own origin (cross-site requests can't add it without CORS), so
    # this blocks CSRF in addition to the SameSite session cookie.
    if (request.url.path.startswith(("/api/", "/auth/dev-login")) and request.method not in SAFE_METHODS
            and request.headers.get("x-cloudops") != "1"):
        return JSONResponse({"detail": "Missing X-CloudOps header."}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET,
    session_cookie="cloudops_session",
    max_age=12 * 3600,
    same_site="lax",
    https_only=config.AUTH_MODE == "oidc",
)
app.include_router(auth.router)
app.include_router(api.router)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "ok"}


@app.get("/auth/config", include_in_schema=False)
def auth_config():
    return {"mode": config.AUTH_MODE, "environment": config.ENVIRONMENT}


if STATIC:
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def spa(path: str):
        candidate = (STATIC / path).resolve()
        if path and candidate.is_file() and STATIC.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})
