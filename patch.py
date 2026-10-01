import re

with open('backend/app/api/firma_frontend.py', 'r', encoding='utf-8') as f:
    content = f.read()

qr_replacement = '''def get_qr_session_status(
    sesid: int,
    db: Session = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
):
    from app.models.sesionqr import SesionQr
    sesion = db.get(SesionQr, sesid)
    if not sesion:
        raise HTTPException(status_code=404, detail="Sesion QR no encontrada")
    if sesion.usrid != principal.user_id and "GESTOR" not in principal.roles and "ADMIN" not in principal.roles:
        raise HTTPException(status_code=403, detail="No tiene permisos para consultar esta sesion QR")
    estado = qr_service.consultar_estado_qr(db, sesid)'''

content = re.sub(r'def get_qr_session_status\([^:]+:\n\s+estado = qr_service\.consultar_estado_qr\(db, sesid\)', qr_replacement, content)

with open('backend/app/api/firma_frontend.py', 'w', encoding='utf-8') as f:
    f.write(content)
