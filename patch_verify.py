import re

with open('backend/app/core/identity.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('verify=settings.ALFRESCO_VERIFY_SSL', 'verify=settings.ALFRESCO_CA_BUNDLE if settings.ALFRESCO_VERIFY_SSL and settings.ALFRESCO_CA_BUNDLE else settings.ALFRESCO_VERIFY_SSL')

with open('backend/app/core/identity.py', 'w', encoding='utf-8') as f:
    f.write(content)
