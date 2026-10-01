import re

with open('backend/app/api/auth.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('user_id=user_raw,', 'user_id=snapshot.usrid,')
content = content.replace('nombre_completo=nomcom,', 'nombre_completo=snapshot.nomcom,')
content = content.replace('correo=correo,', 'correo=snapshot.correo,')

with open('backend/app/api/auth.py', 'w', encoding='utf-8') as f:
    f.write(content)
