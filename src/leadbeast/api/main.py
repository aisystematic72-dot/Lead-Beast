"""ASGI entry point: JSON API + server-rendered web UI on one app."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from leadbeast import __version__
from leadbeast.api.routes import router as api_router
from leadbeast.core.errors import BudgetExceeded, LeadBeastError, NotFound, PolicyDenied
from leadbeast.core.logging import configure
from leadbeast.web.routes import router as web_router

STATUS = {NotFound: 404, PolicyDenied: 403, BudgetExceeded: 402}


def create_app() -> FastAPI:
    configure()
    app = FastAPI(title="LeadBeast", version=__version__, docs_url="/api/docs",
                  openapi_url="/api/openapi.json")

    @app.exception_handler(LeadBeastError)
    async def _lb_error(_: Request, e: LeadBeastError) -> JSONResponse:
        code = next((v for k, v in STATUS.items() if isinstance(e, k)), 422)
        details = {"reasons": e.reasons} if isinstance(e, PolicyDenied) else {}
        return JSONResponse({"code": e.code, "message": str(e), "details": details},
                            status_code=code)

    @app.exception_handler(RequestValidationError)
    async def _val(_: Request, e: RequestValidationError) -> JSONResponse:
        return JSONResponse({"code": "validation_failed", "message": "invalid request",
                             "details": {"errors": e.errors()}}, status_code=422)

    @app.exception_handler(HTTPException)
    async def _http(request: Request, e: HTTPException) -> Response:
        if e.status_code == 401 and not request.url.path.startswith("/api"):
            return RedirectResponse("/login", status_code=303)
        return JSONResponse({"code": f"http_{e.status_code}", "message": str(e.detail),
                             "details": {}}, status_code=e.status_code)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(api_router)
    app.include_router(web_router)
    app.mount("/static", StaticFiles(directory=Path(__file__).parent.parent / "web" / "static"),
              name="static")
    return app


app = create_app()
