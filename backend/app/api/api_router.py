from fastapi import APIRouter
from app.api import health, alfresco, documentos, plantillas

api_router = APIRouter()

api_router.include_router(
    health.router,
    tags=["health"]
)

# Estos routers deben quedar habilitados si forman parte de la API actual:
api_router.include_router(
    alfresco.router,
    prefix="/alfresco",
    tags=["alfresco"]
)

api_router.include_router(
    documentos.router,
    prefix="/documentos",
    tags=["documentos"]
)

api_router.include_router(
    plantillas.router,
    prefix="/plantillas",
    tags=["plantillas"]
)