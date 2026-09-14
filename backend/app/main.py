from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.requests import Request
from fastapi.staticfiles import StaticFiles
from app.api.api_router import api_router
from app.core.config import settings

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Backend para FirmaDoc, integración con Alfresco y firma de PDF",
    version="1.0.0",
)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": "Ocurrió un error interno en el servidor."},
    )

app.include_router(api_router, prefix="/api")

_ROOT_DIR = Path(__file__).resolve().parents[2]
_FRONTEND_DIR = _ROOT_DIR / "frontend"
_STATIC_DIR = _FRONTEND_DIR / "static"
_TEMPLATES_DIR = _FRONTEND_DIR / "templates"

if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


def _frontend_file(name: str) -> FileResponse:
    path = _TEMPLATES_DIR / name
    return FileResponse(path)


@app.get("/", include_in_schema=False)
async def index_redirect():
    return RedirectResponse(url="/documentos")


@app.get("/documentos", include_in_schema=False)

async def documents_page():
    return _frontend_file("documentos.html")


@app.get("/documentos/preparar", include_in_schema=False)
async def preparation_page():
    return _frontend_file("preparar.html")


@app.get("/firmas/{firid}", include_in_schema=False)
async def signature_page(firid: int):
    return _frontend_file("firma.html")


@app.get("/pendientes", include_in_schema=False)
async def pending_page():
    return _frontend_file("pendientes.html")


@app.get("/firma-movil/{token}", include_in_schema=False)
async def mobile_signature_page(token: str):
    return _frontend_file("firma_movil.html")
