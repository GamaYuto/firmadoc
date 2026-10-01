import re

with open('frontend/static/js/pendientes.js', 'r', encoding='utf-8') as f:
    content = f.read()

content = re.sub(r'asignados a ti\$\{escapeText\(currentUser\)\}', 'asignados a ti', content)

with open('frontend/static/js/pendientes.js', 'w', encoding='utf-8') as f:
    f.write(content)
