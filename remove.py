import re

with open('backend/tests/conftest.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = re.sub(r'import app\.api\.auth as auth_mod\n.*FakeIdentityResolver\(\)', '', content, flags=re.DOTALL)

with open('backend/tests/conftest.py', 'w', encoding='utf-8') as f:
    f.write(content)
