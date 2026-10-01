import re

with open('backend/app/api/auth.py', 'r', encoding='utf-8') as f:
    content = f.read()

replacement = '''    user_raw = payload.user_id.strip()
    if not user_raw:
        raise HTTPException(status_code=400, detail="El identificador de usuario no puede estar vacio")
        
    password = payload.password or ""

    try:
        snapshot = _resolver.resolve_user(user_raw, password)
    except IdentityResolutionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    # In a real environment, roles would come from Alfresco groups.
    # We will grant GESTOR to allow using the tool for now.
    roles = ("EMPLEADO", "GESTOR")'''

content = re.sub(r'    user_raw = payload\.user_id\.strip\(\)\n.*?roles = \("EMPLEADO", "GESTOR"\)', replacement, content, flags=re.DOTALL)

with open('backend/app/api/auth.py', 'w', encoding='utf-8') as f:
    f.write(content)
