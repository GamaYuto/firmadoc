import re

with open('frontend/static/js/iniciar.js', 'r', encoding='utf-8') as f:
    content = f.read()

content = re.sub(r'\);\n  \}\n  \n\n  viewer = ', 'viewer = ', content)

with open('frontend/static/js/iniciar.js', 'w', encoding='utf-8') as f:
    f.write(content)
