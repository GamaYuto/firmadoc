from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.requests import Request
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