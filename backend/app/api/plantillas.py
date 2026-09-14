from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from app.core.database import get_db
from app.core.security import AuthenticatedPrincipal, get_current_principal
from app.schemas.plantilla import PlantillaCreate, PlantillaUpdate, PlantillaRead, PlantillaList
from app.schemas.campo import CampoCreate, CampoUpdate, CampoRead, CampoList
from app.services.template_service import TemplateService
from app.crud import crud_plantill, crud_tplcamp

router = APIRouter()

@router.post("", response_model=PlantillaRead, status_code=201)
def create_plantilla(
    request: Request,
    plantilla_in: PlantillaCreate,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal)
):
    return TemplateService.create_plantilla(db, plantilla_in, principal.user_id, request.client.host)

@router.get("", response_model=PlantillaList)
def get_plantillas(
    skip: int = 0,
    limit: int = 50,
    estado: Optional[str] = None,
    tplcod: Optional[str] = None,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal)
):
    items, total = crud_plantill.get_plantillas(db, skip=skip, limit=limit, estado=estado, tplcod=tplcod)
    return {"items": items, "total": total}

@router.get("/{tplid}", response_model=PlantillaRead)
def get_plantilla(
    tplid: int,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal)
):
    tpl = crud_plantill.get_plantilla(db, tplid)
    if not tpl:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
    return tpl

@router.patch("/{tplid}", response_model=PlantillaRead)
def update_plantilla(
    request: Request,
    tplid: int,
    plantilla_in: PlantillaUpdate,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal)
):
    return TemplateService.update_plantilla(db, tplid, plantilla_in, principal.user_id, request.client.host)

@router.post("/{tplid}/versiones", response_model=PlantillaRead, status_code=201)
def create_new_version(
    request: Request,
    tplid: int,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal)
):
    return TemplateService.create_new_version(db, tplid, principal.user_id, request.client.host)

@router.post("/{tplid}/activar", response_model=PlantillaRead)
def activate_plantilla(
    request: Request,
    tplid: int,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal)
):
    return TemplateService.activate_plantilla(db, tplid, principal.user_id, request.client.host)

@router.post("/{tplid}/inactivar", response_model=PlantillaRead)
def deactivate_plantilla(
    request: Request,
    tplid: int,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal)
):
    return TemplateService.deactivate_plantilla(db, tplid, principal.user_id, request.client.host)

# Campos
@router.post("/{tplid}/campos", response_model=CampoRead, status_code=201)
def create_campo(
    request: Request,
    tplid: int,
    campo_in: CampoCreate,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal)
):
    return TemplateService.create_campo(db, tplid, campo_in, principal.user_id, request.client.host)

@router.get("/{tplid}/campos", response_model=CampoList)
def get_campos(
    tplid: int,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal)
):
    items = crud_tplcamp.list_campos_by_template(db, tplid)
    return {"items": items, "total": len(items)}

@router.get("/{tplid}/campos/{camid}", response_model=CampoRead)
def get_campo(
    tplid: int,
    camid: int,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal)
):
    campo = crud_tplcamp.get_campo(db, camid)
    if not campo or campo.tplid != tplid:
        raise HTTPException(status_code=404, detail="Campo no encontrado")
    return campo

@router.patch("/{tplid}/campos/{camid}", response_model=CampoRead)
def update_campo(
    request: Request,
    tplid: int,
    camid: int,
    campo_in: CampoUpdate,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal)
):
    return TemplateService.update_campo(db, tplid, camid, campo_in, principal.user_id, request.client.host)

@router.post("/{tplid}/campos/{camid}/inactivar", response_model=CampoRead)
def deactivate_campo(
    request: Request,
    tplid: int,
    camid: int,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal)
):
    return TemplateService.deactivate_campo(db, tplid, camid, principal.user_id, request.client.host)
