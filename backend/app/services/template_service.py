from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.crud import crud_plantill, crud_tplcamp
from app.schemas.plantilla import PlantillaCreate, PlantillaUpdate
from app.schemas.campo import CampoCreate, CampoUpdate
from app.models.audifir import Audifir
from pydantic import BaseModel, ValidationError
import json

class ConfigTexto(BaseModel):
    max_length: int
    font_size: int
    multiline: bool

class ConfigCasilla(BaseModel):
    checked_value: str

class ConfigOpcion(BaseModel):
    group: str
    value: str

class ConfigFecha(BaseModel):
    format: str
    automatic: bool

class ConfigFirma(BaseModel):
    role: str
    capture_type: str

def validate_config(camtip: str, config: dict):
    if not config:
        return
    try:
        if camtip in ['TEXTO', 'TEXLAR', 'NOMBRE', 'TIPDOC', 'NUMDOC']:
            ConfigTexto(**config)
        elif camtip == 'CASILLA':
            ConfigCasilla(**config)
        elif camtip == 'OPCION':
            ConfigOpcion(**config)
        elif camtip == 'FECHA':
            ConfigFecha(**config)
        elif camtip in ['FIRMA', 'SELLO']:
            ConfigFirma(**config)
    except ValidationError as e:
        raise ValueError(f"Configuración JSONB inválida para camtip {camtip}: {e}")

def create_evento(db: Session, evento: str, enttip: str, entid: int, usrid: str, iporig: str = None, detalle: str = None):
    db_evento = Audifir(
        evento=evento,
        enttip=enttip,
        entid=entid,
        usrid=usrid,
        iporig=iporig,
        detalle=detalle
    )
    db.add(db_evento)

class TemplateService:

    @staticmethod
    def create_plantilla(db: Session, obj_in: PlantillaCreate, usrcre: str, ip: str = None):
        existing = crud_plantill.get_plantilla_by_code_version(db, obj_in.tplcod, 1)
        if existing:
            raise HTTPException(status_code=409, detail="Ya existe una plantilla con este código y versión 1")
        
        tpl = crud_plantill.create_plantilla(db, obj_in, usrcre)
        db.flush()
        create_evento(db, "TPL_CREA", "PLANTILLA", tpl.tplid, usrcre, ip, f"Plantilla creada: {tpl.tplcod}")
        db.commit()
        db.refresh(tpl)
        return tpl

    @staticmethod
    def update_plantilla(db: Session, tplid: int, obj_in: PlantillaUpdate, usrmod: str, ip: str = None):
        tpl = crud_plantill.get_plantilla(db, tplid)
        if not tpl:
            raise HTTPException(status_code=404, detail="Plantilla no encontrada")
        if tpl.estado != "BORRADOR":
            raise HTTPException(status_code=400, detail="Solo se pueden modificar plantillas en estado BORRADOR")
        
        tpl = crud_plantill.update_plantilla(db, tpl, obj_in, usrmod)
        db.flush()
        create_evento(db, "TPL_MODI", "PLANTILLA", tpl.tplid, usrmod, ip, f"Plantilla modificada")
        db.commit()
        db.refresh(tpl)
        return tpl

    @staticmethod
    def activate_plantilla(db: Session, tplid: int, usrmod: str, ip: str = None):
        tpl = crud_plantill.get_plantilla(db, tplid)
        if not tpl:
            raise HTTPException(status_code=404, detail="Plantilla no encontrada")
        if tpl.estado == "ACTIVA":
            raise HTTPException(status_code=400, detail="La plantilla ya está activa")
        
        # Desactivar versión anterior si existe
        active_tpl = crud_plantill.get_active_plantilla_by_code(db, tpl.tplcod)
        if active_tpl and active_tpl.tplid != tpl.tplid:
            active_tpl.estado = "INACTIVA"
            active_tpl.usrmod = usrmod
            create_evento(db, "TPL_INAC", "PLANTILLA", active_tpl.tplid, usrmod, ip, f"Versión anterior inactivada")
            db.flush()
        
        tpl.estado = "ACTIVA"
        tpl.usrmod = usrmod
        create_evento(db, "TPL_ACTI", "PLANTILLA", tpl.tplid, usrmod, ip, f"Plantilla activada")
        db.commit()
        db.refresh(tpl)
        return tpl

    @staticmethod
    def deactivate_plantilla(db: Session, tplid: int, usrmod: str, ip: str = None):
        tpl = crud_plantill.get_plantilla(db, tplid)
        if not tpl:
            raise HTTPException(status_code=404, detail="Plantilla no encontrada")
        
        tpl.estado = "INACTIVA"
        tpl.usrmod = usrmod
        create_evento(db, "TPL_INAC", "PLANTILLA", tpl.tplid, usrmod, ip, f"Plantilla inactivada manualmente")
        db.commit()
        db.refresh(tpl)
        return tpl

    @staticmethod
    def create_new_version(db: Session, tplid: int, usrcre: str, ip: str = None):
        tpl = crud_plantill.get_plantilla(db, tplid)
        if not tpl:
            raise HTTPException(status_code=404, detail="Plantilla original no encontrada")
        
        # Verificar que no exista ya la nueva versión (para evitar colisiones raras)
        if crud_plantill.get_plantilla_by_code_version(db, tpl.tplcod, tpl.tplver + 1):
            raise HTTPException(status_code=409, detail="La nueva versión ya existe")
            
        new_tpl = crud_plantill.create_new_version(db, tpl, usrcre)
        db.flush()
        
        # Copiar campos activos
        crud_tplcamp.clone_campos_for_version(db, tpl.tplid, new_tpl.tplid, usrcre)
        db.flush()
        
        create_evento(db, "TPL_VERS", "PLANTILLA", new_tpl.tplid, usrcre, ip, f"Nueva versión creada desde v{tpl.tplver}")
        db.commit()
        db.refresh(new_tpl)
        return new_tpl

    @staticmethod
    def create_campo(db: Session, tplid: int, obj_in: CampoCreate, usrcre: str, ip: str = None):
        tpl = crud_plantill.get_plantilla(db, tplid)
        if not tpl:
            raise HTTPException(status_code=404, detail="Plantilla no encontrada")
        if tpl.estado != "BORRADOR":
            raise HTTPException(status_code=400, detail="Solo se pueden agregar campos a plantillas en BORRADOR")
        
        if obj_in.pagina > tpl.numpag:
            raise HTTPException(status_code=422, detail="La página del campo excede el número de páginas de la plantilla")

        if crud_tplcamp.get_campo_by_code(db, tplid, obj_in.camcod):
            raise HTTPException(status_code=409, detail="Ya existe un campo con este código en la plantilla")
        
        if crud_tplcamp.get_campo_by_orden(db, tplid, obj_in.orden):
            raise HTTPException(status_code=409, detail="Ya existe un campo con este orden en la plantilla")

        try:
            validate_config(obj_in.camtip, obj_in.config)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

        campo = crud_tplcamp.create_campo(db, tplid, obj_in, usrcre)
        db.flush()
        create_evento(db, "CAM_CREA", "CAMPO", campo.camid, usrcre, ip, f"Campo {campo.camcod} creado")
        db.commit()
        db.refresh(campo)
        return campo

    @staticmethod
    def update_campo(db: Session, tplid: int, camid: int, obj_in: CampoUpdate, usrmod: str, ip: str = None):
        tpl = crud_plantill.get_plantilla(db, tplid)
        if not tpl or tpl.estado != "BORRADOR":
            raise HTTPException(status_code=400, detail="Solo se pueden modificar campos de plantillas en BORRADOR")
        
        campo = crud_tplcamp.get_campo(db, camid)
        if not campo or campo.tplid != tplid:
            raise HTTPException(status_code=404, detail="Campo no encontrado")

        if obj_in.pagina is not None and obj_in.pagina > tpl.numpag:
            raise HTTPException(status_code=422, detail="La página del campo excede el número de páginas")

        if obj_in.orden is not None and obj_in.orden != campo.orden:
            if crud_tplcamp.get_campo_by_orden(db, tplid, obj_in.orden):
                raise HTTPException(status_code=409, detail="Ya existe un campo con este orden")

        if obj_in.config is not None:
            try:
                validate_config(campo.camtip, obj_in.config)
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e))

        campo = crud_tplcamp.update_campo(db, campo, obj_in, usrmod)
        db.flush()
        create_evento(db, "CAM_MODI", "CAMPO", campo.camid, usrmod, ip, f"Campo {campo.camcod} modificado")
        db.commit()
        db.refresh(campo)
        return campo

    @staticmethod
    def deactivate_campo(db: Session, tplid: int, camid: int, usrmod: str, ip: str = None):
        tpl = crud_plantill.get_plantilla(db, tplid)
        if not tpl or tpl.estado != "BORRADOR":
            raise HTTPException(status_code=400, detail="Solo se pueden inactivar campos de plantillas en BORRADOR")
        
        campo = crud_tplcamp.get_campo(db, camid)
        if not campo or campo.tplid != tplid:
            raise HTTPException(status_code=404, detail="Campo no encontrado")
        
        campo.activo = False
        campo.usrmod = usrmod
        db.flush()
        create_evento(db, "CAM_INAC", "CAMPO", campo.camid, usrmod, ip, f"Campo {campo.camcod} inactivado")
        db.commit()
        db.refresh(campo)
        return campo
