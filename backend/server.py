from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
import sys
import tempfile

from checks import self_check
import config
from config import BACKEND_HOST, BACKEND_PORT, VALIDATE_ASR_ON_START, validate_config, validate_runtime_for_asr


if "--check" in sys.argv:
    validate_config()
    with tempfile.TemporaryDirectory(prefix="contextbubble-check-") as tmpdir:
        with config.settings_override(replace(config.get_settings(), data_dir=Path(tmpdir))):
            self_check()
    print("ok")
    raise SystemExit(0)


from fastapi import FastAPI, Request
from fastapi.responses import Response
from starlette.exceptions import HTTPException as StarletteHTTPException
import uvicorn

import auth
from api_routes import error, json_response, routers
from auth import allowed_origin
from db import init_db
from preparation_jobs import resume_preparations
from translation_jobs import start_translation_worker, stop_translation_worker


@asynccontextmanager
async def lifespan(_app):
    validate_config()
    init_db()
    auth.initialize_auth()
    if VALIDATE_ASR_ON_START:
        validate_runtime_for_asr()
    resume_preparations()
    await start_translation_worker()
    print(f"ContextBubble backend on http://{BACKEND_HOST}:{BACKEND_PORT}")
    print(f"ContextBubble pairing code: {auth.PAIRING_CODE} (expires in 5 minutes)")
    try:
        yield
    finally:
        await stop_translation_worker()


app = FastAPI(lifespan=lifespan)
for router in routers:
    app.include_router(router)


@app.middleware("http")
async def cors_middleware(request, call_next):
    response = Response(status_code=204) if request.method == "OPTIONS" else await call_next(request)
    origin = request.headers.get("origin", "")
    if allowed_origin(origin, request.url.path):
        response.headers["access-control-allow-origin"] = origin
    response.headers["access-control-allow-methods"] = "GET, POST, OPTIONS"
    response.headers["access-control-allow-headers"] = "authorization, content-type"
    response.headers["access-control-allow-private-network"] = "true"
    return response


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc):
    if exc.status_code == 404:
        return json_response({"error": "not found", "error_code": "NOT_FOUND"}, 404)
    return error("HTTP_ERROR", str(exc.detail), exc.status_code)


def main():
    uvicorn.run(app, host=BACKEND_HOST, port=BACKEND_PORT)


if __name__ == "__main__":
    main()
