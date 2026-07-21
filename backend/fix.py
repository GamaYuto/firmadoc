file_path = r'C:\Users\AuxTics\Documents\GitHub\firmadoc\backend\alembic\versions\f58b81150be2_implementar_flujodoc_y_flupaso_docfir_.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()
content = content.replace("op.drop_constraint('ck_audifir_enttip', 'audifir', type_='check')", "op.execute('ALTER TABLE audifir DROP CONSTRAINT IF EXISTS ck_audifir_enttip')")
content = content.replace("op.drop_constraint('ck_docfir_estado', 'docfir', type_='check')", "op.execute('ALTER TABLE docfir DROP CONSTRAINT IF EXISTS ck_docfir_estado')")
with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
