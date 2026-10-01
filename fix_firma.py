import re

with open('frontend/static/js/firma.js', 'r', encoding='utf-8') as f:
    content = f.read()

content = re.sub(r'async function init\(\) \{\n\s*if \(!firid\) throw new Error\("Identificador de firma invalido"\);\n.*?(?=  viewer = new PdfViewer)', 'async function init() {\n  if (!firid) throw new Error("Identificador de firma invalido");\n\n', content, flags=re.DOTALL)

with open('frontend/static/js/firma.js', 'w', encoding='utf-8') as f:
    f.write(content)
