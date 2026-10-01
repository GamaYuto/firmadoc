import os, re
for root, dirs, files in os.walk('frontend/static/js'):
    for f in files:
        if f.endswith('.js'):
            path = os.path.join(root, f)
            try:
                with open(path, 'r', encoding='utf-8') as file:
                    content = file.read()
            except Exception:
                continue
            
            content = re.sub(r',\s*setSessionUser', '', content)
            content = re.sub(r',\s*getCurrentUser', '', content)
            content = re.sub(r'(?:await\s+)?setSessionUser\([^)]*\);?', '', content)
            content = re.sub(r'const user = params\.get\([\'"]user[\'"]\).*?;', '', content)
            content = re.sub(r'\?user=\$\{encodeURIComponent\([^)]+\)\}', '', content)
            content = re.sub(r'&user=\$\{encodeURIComponent\([^)]+\)\}', '', content)
            content = re.sub(r',\s*\{\s*user:\s*getCurrentUser\([^)]*\)\s*\}', '', content)
            content = re.sub(r'user:\s*getCurrentUser\([^)]*\),?', '', content)
            content = re.sub(r'let redirectUrl = "/firmas/" \+ firid \+ "\?user=" \+ encodeURIComponent\(userInput\.value\.trim\(\)\);', 'let redirectUrl = "/firmas/" + firid;', content)

            with open(path, 'w', encoding='utf-8') as file:
                file.write(content)
