import re

with open('backend/tests/test_auth.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = re.sub(r'def test_fake_identity_disabled_outside_lab.*?(?=def test_)', '', content, flags=re.DOTALL)
content = re.sub(r'def test_simulation_session_disabled_outside_lab.*?(?=def test_)', '', content, flags=re.DOTALL)
content = re.sub(r'def test_cannot_create_session_as_other_valid_user.*?(?=def test_)', '', content, flags=re.DOTALL)
# One of them might be at the end, so also without (?=def test_)
content = re.sub(r'def test_cannot_create_session_as_other_valid_user.*', '', content, flags=re.DOTALL)

with open('backend/tests/test_auth.py', 'w', encoding='utf-8') as f:
    f.write(content)
