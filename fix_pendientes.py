import re

with open('frontend/static/js/pendientes.js', 'r', encoding='utf-8') as f:
    content = f.read()

content = re.sub(r'const userInput = document\.querySelector\("\[data-user-input\]"\);\n\nconst params = new URLSearchParams\(window\.location\.search\);\nconst initialUser = params\.get\("user"\) \|\| "firmante";\n', '', content)
content = re.sub(r'const currentUser = userInput\?\.value\?\.trim\(\) \|\| initialUser;\n', '', content)
content = content.replace('asignados a ', 'asignados a ti')

with open('frontend/static/js/pendientes.js', 'w', encoding='utf-8') as f:
    f.write(content)
