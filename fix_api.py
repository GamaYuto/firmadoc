import re

with open('frontend/static/js/api.js', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('""": "&quot;",', '"\\"": "&quot;",')

with open('frontend/static/js/api.js', 'w', encoding='utf-8') as f:
    f.write(content)
