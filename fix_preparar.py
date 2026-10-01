import re

with open('frontend/static/js/preparar.js', 'r', encoding='utf-8') as f:
    content = f.read()

content = re.sub(r'\s*\}\);\n\s*\}\n\s*preparation = ', '\n  preparation = ', content)

with open('frontend/static/js/preparar.js', 'w', encoding='utf-8') as f:
    f.write(content)
