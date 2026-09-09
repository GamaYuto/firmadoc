"""implementar_docfirma_y_firpos

Revision ID: 27c5eb2328af
Revises: f61284a6c891
Create Date: 2026-07-28 13:11:45.158799

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '27c5eb2328af'
down_revision: Union[str, Sequence[str], None] = 'f61284a6c891'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Preflight check for duplicates on docfir.nodid
    connection = op.get_bind()
    result = connection.execute(sa.text("""
        SELECT nodid, COUNT(*) FROM docfir 
        WHERE estado IN ('BORRADOR', 'PREPARADO', 'EN_CURSO', 'PENDIENTE_FIRMA', 'FIRMADO_PARCIAL', 'PENDIENTE_PUBLICACION', 'ERROR_PUBLICACION')
        GROUP BY nodid HAVING COUNT(*) > 1
    """)).fetchall()
    if result:
        raise Exception(f"La migración se cancela: existen duplicados activos para nodid: {result}")

    # 2. create docfirma table
    op.create_table('docfirma',
        sa.Column('firid', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('docid', sa.BigInteger(), nullable=False),
        sa.Column('parid', sa.BigInteger(), nullable=False),
        sa.Column('opeid', sa.UUID(), nullable=False),
        sa.Column('secuen', sa.Integer(), nullable=False),
        sa.Column('intnum', sa.Integer(), nullable=False),
        sa.Column('tipfir', sa.String(length=30), nullable=False),
        sa.Column('estado', sa.String(length=20), nullable=False),
        sa.Column('verori', sa.String(length=20), nullable=False),
        sa.Column('hasori', sa.String(length=64), nullable=False),
        sa.Column('verfin', sa.String(length=20), nullable=True),
        sa.Column('hasfin', sa.String(length=64), nullable=True),
        sa.Column('errcod', sa.String(length=40), nullable=True),
        sa.Column('motivo', sa.String(length=500), nullable=True),
        sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('fecini', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fecfin', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revnum', sa.Integer(), nullable=False),
        sa.Column('usrcre', sa.String(length=60), nullable=False),
        sa.Column('feccre', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('usrmod', sa.String(length=60), nullable=True),
        sa.Column('fecmod', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("estado != 'CANCELADA' OR (motivo IS NOT NULL AND TRIM(motivo) != '')", name='ck_docfirma_motivo_cancelada'),
        sa.CheckConstraint("estado != 'COMPLETADA' OR (errcod IS NULL AND motivo IS NULL)", name='ck_docfirma_completada_limpia'),
        sa.CheckConstraint("estado != 'CONFLICTO' OR errcod IS NOT NULL", name='ck_docfirma_errcod_conflicto'),
        sa.CheckConstraint("estado != 'FALLIDA' OR errcod IS NOT NULL", name='ck_docfirma_errcod_fallida'),
        sa.CheckConstraint("estado IN ('COMPLETADA','CONFLICTO','FALLIDA','CANCELADA') OR fecfin IS NULL", name='ck_docfirma_fecfin_operativo'),
        sa.CheckConstraint("estado IN ('INICIADA', 'GENERADA', 'SUBIENDO', 'CARGADA', 'VERIFICANDO', 'COMPLETADA', 'CONFLICTO', 'FALLIDA', 'CANCELADA')", name='ck_docfirma_estado'),
        sa.CheckConstraint("estado NOT IN ('CARGADA', 'VERIFICANDO', 'COMPLETADA') OR verfin IS NOT NULL", name='ck_docfirma_verfin_req'),
        sa.CheckConstraint("estado NOT IN ('COMPLETADA','CONFLICTO','FALLIDA','CANCELADA') OR fecfin IS NOT NULL", name='ck_docfirma_fecfin_terminal'),
        sa.CheckConstraint("estado NOT IN ('GENERADA', 'SUBIENDO', 'CARGADA', 'VERIFICANDO', 'COMPLETADA') OR hasfin IS NOT NULL", name='ck_docfirma_hasfin_req'),
        sa.CheckConstraint("estado NOT IN ('VERIFICANDO', 'COMPLETADA') OR (result IS NOT NULL AND result != 'null'::jsonb)", name='ck_docfirma_result_req'),
        sa.CheckConstraint("hasfin IS NULL OR hasfin ~ '^[0-9a-f]{64}$'", name='ck_docfirma_hasfin_hex'),
        sa.CheckConstraint("hasori ~ '^[0-9a-f]{64}$'", name='ck_docfirma_hasori_hex'),
        sa.CheckConstraint("tipfir IN ('MANUSCRITA', 'INTERNA')", name='ck_docfirma_tipfir'),
        sa.CheckConstraint('fecfin IS NULL OR fecfin >= fecini', name='ck_docfirma_fechas'),
        sa.CheckConstraint('intnum > 0', name='ck_docfirma_intnum_pos'),
        sa.CheckConstraint('revnum >= 1', name='ck_docfirma_revnum_pos'),
        sa.CheckConstraint('secuen > 0', name='ck_docfirma_secuen_pos'),
        sa.ForeignKeyConstraint(['docid'], ['docfir.docid'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['parid'], ['docpart.parid'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('firid'),
        sa.UniqueConstraint('docid', 'secuen', name='uq_docfirma_docid_secuen'),
        sa.UniqueConstraint('opeid'),
        sa.UniqueConstraint('parid', 'intnum', name='uq_docfirma_parid_intnum')
    )
    op.create_index('ix_docfirma_estado_fecmod', 'docfirma', ['estado', 'fecmod'], unique=False)
    op.create_index('uq_docfirma_docid_activa', 'docfirma', ['docid'], unique=True, postgresql_where=sa.text("coalesce(estado, '') IN ('INICIADA', 'GENERADA', 'SUBIENDO', 'CARGADA', 'VERIFICANDO')"))
    op.create_index('uq_docfirma_parid_compl', 'docfirma', ['parid'], unique=True, postgresql_where=sa.text("estado = 'COMPLETADA'"))

    # 3. create firpos table
    op.create_table('firpos',
        sa.Column('posid', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('firid', sa.BigInteger(), nullable=False),
        sa.Column('pagina', sa.Integer(), nullable=False),
        sa.Column('posx', sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column('posy', sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column('ancho', sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column('alto', sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column('rotaci', sa.Integer(), nullable=False),
        sa.Column('orden', sa.Integer(), nullable=False),
        sa.Column('camid', sa.BigInteger(), nullable=True),
        sa.Column('feccre', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('alto > 0', name='ck_firpos_alto'),
        sa.CheckConstraint('ancho > 0', name='ck_firpos_ancho'),
        sa.CheckConstraint('orden > 0', name='ck_firpos_orden'),
        sa.CheckConstraint('pagina > 0', name='ck_firpos_pagina'),
        sa.CheckConstraint('posx >= 0', name='ck_firpos_posx'),
        sa.CheckConstraint('posy >= 0', name='ck_firpos_posy'),
        sa.CheckConstraint('rotaci IN (0, 90, 180, 270)', name='ck_firpos_rotaci'),
        sa.ForeignKeyConstraint(['camid'], ['tplcamp.camid'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['firid'], ['docfirma.firid'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('posid'),
        sa.UniqueConstraint('firid', 'orden', name='uq_firpos_firid_orden')
    )

    # 4. create uq_docfir_nodid_activo index
    op.create_index('uq_docfir_nodid_activo', 'docfir', ['nodid'], unique=True, postgresql_where=sa.text("estado IN ('BORRADOR', 'PREPARADO', 'EN_CURSO', 'PENDIENTE_FIRMA', 'FIRMADO_PARCIAL', 'PENDIENTE_PUBLICACION', 'ERROR_PUBLICACION')"))

    # 5. modify ck_audifir_enttip
    op.drop_constraint('ck_audifir_enttip', 'audifir', type_='check')
    op.create_check_constraint(
        'ck_audifir_enttip',
        'audifir',
        "enttip IN ('DOCUMENTO', 'PLANTILLA', 'CAMPO', 'SISTEMA', 'FLUJODOC', 'FLUPASO', 'PASO', 'PARTICIPANTE', 'FIRMA')"
    )


def downgrade() -> None:
    # 1. Downgrade check for audifir events with enttip = 'FIRMA'
    connection = op.get_bind()
    result = connection.execute(sa.text("SELECT COUNT(*) FROM audifir WHERE enttip = 'FIRMA'")).scalar()
    if result > 0:
        raise Exception("No se puede hacer downgrade: existen eventos de auditoría con enttip = 'FIRMA'")

    # 2. restore ck_audifir_enttip constraint
    op.drop_constraint('ck_audifir_enttip', 'audifir', type_='check')
    op.create_check_constraint(
        'ck_audifir_enttip',
        'audifir',
        "enttip IN ('DOCUMENTO', 'PLANTILLA', 'CAMPO', 'SISTEMA', 'FLUJODOC', 'FLUPASO', 'PASO', 'PARTICIPANTE')"
    )

    # 3. drop indices and tables
    op.drop_index('uq_docfir_nodid_activo', table_name='docfir', postgresql_where=sa.text("estado IN ('BORRADOR', 'PREPARADO', 'EN_CURSO', 'PENDIENTE_FIRMA', 'FIRMADO_PARCIAL', 'PENDIENTE_PUBLICACION', 'ERROR_PUBLICACION')"))
    op.drop_table('firpos')
    op.drop_index('uq_docfirma_parid_compl', table_name='docfirma', postgresql_where=sa.text("estado = 'COMPLETADA'"))
    op.drop_index('uq_docfirma_docid_activa', table_name='docfirma', postgresql_where=sa.text("coalesce(estado, '') IN ('INICIADA', 'GENERADA', 'SUBIENDO', 'CARGADA', 'VERIFICANDO')"))
    op.drop_index('ix_docfirma_estado_fecmod', table_name='docfirma')
    op.drop_table('docfirma')
