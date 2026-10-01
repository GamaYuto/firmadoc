import os, re
for root, dirs, files in os.walk('frontend/static/js'):
    for f in files:
        if f.endswith('.js'):
            path = os.path.join(root, f)
            with open(path, 'r', encoding='utf-8') as file:
                content = file.read()
            
            # Remove block if (userInput) { ... }
            content = re.sub(r'if\s*\(userInput\)\s*\{[^\}]+\}\s*', '', content)
            
            # Remove userInput listeners
            content = re.sub(r'userInput\?\.addEventListener[^;]+;', '', content)
            
            # Remove user = params.get(...) if leftover
            content = re.sub(r'const user = params\.get\([\'"]user[\'"]\)[^;]+;', '', content)
            
            # Remove any user input setting like userInput.value = ...
            content = re.sub(r'if\s*\(userInput\)\s*userInput\.value[^;]+;', '', content)
            content = re.sub(r'userInput\.value\s*=[^;]+;', '', content)
            
            # In iniciar.js: viewer.setSigner(e.target.value === "otro" ? "" : userInput.value.trim());
            # Wait, if "Yo" is selected, who is the signer? We can just pass "yo" or let backend handle it, or use session user.
            content = re.sub(r'userInput\.value\.trim\(\)', '""', content)

            with open(path, 'w', encoding='utf-8') as file:
                file.write(content)
