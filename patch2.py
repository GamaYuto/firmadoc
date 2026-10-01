import re

# Update API endpoint
with open('backend/app/api/firma_frontend.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('frontend_signature_service.refresh_preparation(db, docid)', 'frontend_signature_service.refresh_preparation(db, docid, principal)')

with open('backend/app/api/firma_frontend.py', 'w', encoding='utf-8') as f:
    f.write(content)

# Update Service
with open('backend/app/services/frontend_signature_service.py', 'r', encoding='utf-8') as f:
    content = f.read()

service_replacement = '''    async def refresh_preparation(self, db: Session, docid: int, actor) -> PreparationRead:
        doc = self._get_doc(db, docid)
        
        has_access = False
        if "GESTOR" in actor.roles or "ADMIN" in actor.roles:
            has_access = True
        elif doc.usrmod == actor.user_id or doc.usralt == actor.user_id:
            has_access = True
        else:
            participant = db.execute(select(DocPart).where(DocPart.docid == docid, DocPart.usrid == actor.user_id)).scalar_one_or_none()
            if participant:
                has_access = True
        
        if not has_access:
            raise HTTPException(status_code=403, detail="No tiene permisos para ver la preparacion de este documento")
            
        step = self._get_signing_step(db, doc.docid)'''

content = re.sub(r'    async def refresh_preparation\(self, db: Session, docid: int\) -> PreparationRead:\n        doc = self\._get_doc\(db, docid\)\n        step = self\._get_signing_step\(db, doc\.docid\)', service_replacement, content)

with open('backend/app/services/frontend_signature_service.py', 'w', encoding='utf-8') as f:
    f.write(content)
