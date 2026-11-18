from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders

from . import models  # noqa: F401  (registers tables)
from .config import settings
from .database import Base, engine
from .migrate import ensure_columns
from .routers import admin, auth, menu, orders, reports, reservations, restaurant

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Day 1: create tables directly. Switch to Alembic migrations before production.
    settings.validate_for_environment()
    Base.metadata.create_all(engine)
    ensure_columns(engine)  # keeps databases from earlier days working
    yield


app = FastAPI(title=f"{settings.app_name} API", version="0.1.0", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(restaurant.router)
app.include_router(menu.router)
app.include_router(orders.router)
app.include_router(reservations.router)
app.include_router(reports.router)
app.include_router(admin.router)


class SecurityHeadersMiddleware:
    """Adds secure response headers (spec 17.3). Written as plain ASGI so it never interferes
    with background tasks such as email sending."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_api = scope.get("path", "").startswith("/api/")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                if is_api:
                    headers.setdefault("Cache-Control", "no-store")  # API data is private
            await send(message)

        await self.app(scope, receive, send_with_headers)


app.add_middleware(SecurityHeadersMiddleware)


@app.get("/api/v1/health", tags=["system"])
def health():
    return {"status": "ok", "service": settings.app_name}


@app.get("/r/{slug}", include_in_schema=False)
def public_site(slug: str):
    # Tenant is resolved in the browser via /api/v1/public/restaurants/{slug}
    return FileResponse(STATIC_DIR / "public.html")


@app.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(STATIC_DIR / "admin.html")


@app.get("/r/{slug}/track", include_in_schema=False)
def track_page(slug: str):
    return FileResponse(STATIC_DIR / "track.html")


@app.get("/r/{slug}/menu", include_in_schema=False)
def public_menu_page(slug: str):
    return FileResponse(STATIC_DIR / "menu.html")


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
