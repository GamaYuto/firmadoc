import re

with open('backend/app/core/identity.py', 'r', encoding='utf-8') as f:
    content = f.read()

replacement = '''def get_identity_resolver(is_testing: bool = False) -> IdentityResolver:
    import os
    if is_testing or os.getenv("TESTING") == "1":
        return FakeIdentityResolver()
    return AlfrescoIdentityResolver()'''

content = re.sub(r'def get_identity_resolver\(is_testing: bool = False\) -> IdentityResolver:\n    if is_testing:\n        return FakeIdentityResolver\(\)\n    return AlfrescoIdentityResolver\(\)', replacement, content)

with open('backend/app/core/identity.py', 'w', encoding='utf-8') as f:
    f.write(content)
