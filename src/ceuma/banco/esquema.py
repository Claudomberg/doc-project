import sqlite3


VERSAO_ESQUEMA = 2


def verificar_versao(conexao):
    versao = conexao.execute("PRAGMA user_version").fetchone()[0]
    if not 0 <= versao <= VERSAO_ESQUEMA:
        raise sqlite3.DatabaseError("Versão de esquema SQLite incompatível com o CEUMA.")
    return versao


def ativar_chaves_estrangeiras(conexao):
    conexao.execute("PRAGMA foreign_keys = ON")
    if not conexao.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise sqlite3.IntegrityError("A operação requer chaves estrangeiras ativas.")


def migrar_esquema(conexao, esquema_base):
    if conexao.in_transaction:
        raise sqlite3.ProgrammingError("A migração exige uma conexão sem transação ativa.")
    ativar_chaves_estrangeiras(conexao)
    if verificar_versao(conexao) == VERSAO_ESQUEMA:
        return

    with conexao:
        conexao.execute("BEGIN IMMEDIATE")
        versao = verificar_versao(conexao)
        if versao == VERSAO_ESQUEMA:
            return
        if versao == 0:
            for sql in esquema_base.split(";"):
                if sql.strip():
                    conexao.execute(sql)
        for destino in range(versao + 1, VERSAO_ESQUEMA + 1):
            for sql in _MIGRACOES[destino]:
                conexao.execute(sql)
        if conexao.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise sqlite3.IntegrityError("O banco contém relacionamentos inválidos.")
        conexao.execute(f"PRAGMA user_version = {VERSAO_ESQUEMA}")


_MIGRACAO_1 = (
    """ALTER TABLE analises ADD COLUMN modo TEXT NOT NULL DEFAULT 'INDIVIDUAL'
       CHECK (modo IN ('INDIVIDUAL', 'FAMILIAR'))""",
    """ALTER TABLE documentos ADD COLUMN escopo TEXT
       CHECK (escopo IN ('INDIVIDUAL', 'COMPARTILHADO', 'INCONCLUSIVO'))""",
    """CREATE TABLE pessoas (
           id INTEGER PRIMARY KEY,
           analise_id INTEGER NOT NULL REFERENCES analises(id) ON DELETE CASCADE,
           papel TEXT NOT NULL CHECK (papel IN ('ESTUDANTE_TITULAR', 'INTEGRANTE', 'INDEFINIDO')),
           UNIQUE (analise_id, id)
       )""",
    """CREATE UNIQUE INDEX idx_pessoas_titular ON pessoas(analise_id)
       WHERE papel = 'ESTUDANTE_TITULAR'""",
    "CREATE UNIQUE INDEX idx_documentos_processo ON documentos(analise_id, id)",
    """CREATE TABLE documentos_pessoas (
           analise_id INTEGER NOT NULL,
           documento_id INTEGER NOT NULL,
           pessoa_id INTEGER NOT NULL,
           PRIMARY KEY (analise_id, documento_id, pessoa_id),
           FOREIGN KEY (analise_id, documento_id)
               REFERENCES documentos(analise_id, id) ON DELETE CASCADE,
           FOREIGN KEY (analise_id, pessoa_id)
               REFERENCES pessoas(analise_id, id) ON DELETE CASCADE
       )""",
    """CREATE INDEX idx_documentos_pessoas_pessoa
       ON documentos_pessoas(analise_id, pessoa_id, documento_id)""",
    "ALTER TABLE conferencias ADD COLUMN pessoa_id INTEGER REFERENCES pessoas(id) ON DELETE CASCADE",
    "ALTER TABLE conferencias ADD COLUMN documento_1_id INTEGER REFERENCES documentos(id) ON DELETE CASCADE",
    "ALTER TABLE conferencias ADD COLUMN documento_2_id INTEGER REFERENCES documentos(id) ON DELETE CASCADE",
    """CREATE UNIQUE INDEX idx_conferencias_par_campo
       ON conferencias(analise_id, COALESCE(pessoa_id, 0), documento_1_id,
                       documento_2_id, campo_verificado)
       WHERE documento_1_id IS NOT NULL""",
    """CREATE TRIGGER analises_modo_fixo BEFORE UPDATE OF modo ON analises
       WHEN OLD.modo <> NEW.modo
       BEGIN SELECT RAISE(ABORT, 'A modalidade da análise é fixa.'); END""",
    """CREATE TRIGGER documentos_analise_fixa BEFORE UPDATE OF analise_id ON documentos
       WHEN OLD.analise_id <> NEW.analise_id
       BEGIN SELECT RAISE(ABORT, 'O processo do documento é fixo.'); END""",
    # Um INSERT da versão anterior omite escopo; o valor nulo continua válido
    # para leituras e lotes individuais, inclusive os históricos.
    """CREATE TRIGGER documentos_escopo_familiar BEFORE INSERT ON documentos
       WHEN NEW.escopo IS NULL AND
            (SELECT modo FROM analises WHERE id = NEW.analise_id) = 'FAMILIAR'
       BEGIN SELECT RAISE(ABORT, 'Use uma versão com suporte a análises familiares.'); END""",
    """CREATE TRIGGER documentos_validar_escopo BEFORE UPDATE OF escopo ON documentos
       WHEN (NEW.escopo IS NULL AND
             (SELECT modo FROM analises WHERE id = NEW.analise_id) = 'FAMILIAR')
         OR ((NEW.escopo IS NULL OR NEW.escopo = 'INCONCLUSIVO') AND EXISTS (
             SELECT 1 FROM documentos_pessoas WHERE analise_id = OLD.analise_id
                                               AND documento_id = OLD.id))
         OR (NEW.escopo = 'INDIVIDUAL' AND (
             SELECT COUNT(*) FROM documentos_pessoas WHERE analise_id = OLD.analise_id
                                                     AND documento_id = OLD.id) > 1)
       BEGIN SELECT RAISE(ABORT, 'Escopo documental incompatível.'); END""",
    """CREATE TRIGGER documentos_pessoas_validar BEFORE INSERT ON documentos_pessoas
       WHEN NOT EXISTS (
           SELECT 1 FROM documentos
           WHERE id = NEW.documento_id AND analise_id = NEW.analise_id
             AND (escopo = 'COMPARTILHADO' OR (escopo = 'INDIVIDUAL' AND NOT EXISTS (
                  SELECT 1 FROM documentos_pessoas
                  WHERE analise_id = NEW.analise_id AND documento_id = NEW.documento_id)))
       )
       BEGIN SELECT RAISE(ABORT, 'Atribuição incompatível com o documento.'); END""",
    """CREATE TRIGGER documentos_pessoas_recriar BEFORE UPDATE ON documentos_pessoas
       BEGIN SELECT RAISE(ABORT, 'Substitua o vínculo em uma transação.'); END""",
    """CREATE TRIGGER documentos_pessoas_invalidar AFTER DELETE ON documentos_pessoas
       BEGIN
           DELETE FROM conferencias
           WHERE analise_id = OLD.analise_id AND pessoa_id = OLD.pessoa_id
             AND (documento_1_id = OLD.documento_id OR documento_2_id = OLD.documento_id);
       END""",
    """CREATE TRIGGER documentos_escopo_invalidar AFTER UPDATE OF escopo ON documentos
       WHEN OLD.escopo IS NOT NEW.escopo
       BEGIN
           DELETE FROM conferencias
           WHERE analise_id = OLD.analise_id AND pessoa_id IS NOT NULL
             AND (documento_1_id = OLD.id OR documento_2_id = OLD.id);
       END""",
    *(
        f"""CREATE TRIGGER conferencias_validar_{operacao.lower()} BEFORE {operacao} ON conferencias
        WHEN ((SELECT modo FROM analises WHERE id = NEW.analise_id) = 'FAMILIAR'
              AND NEW.observacao LIKE 'AUTO:%')
          OR (NEW.documento_1_id IS NULL AND (NEW.documento_2_id IS NOT NULL OR
                 NEW.pessoa_id IS NOT NULL OR (SELECT modo FROM analises
                 WHERE id = NEW.analise_id) = 'FAMILIAR'))
          OR (NEW.documento_1_id IS NOT NULL AND (
                NEW.documento_2_id IS NULL OR NEW.documento_1_id >= NEW.documento_2_id
                OR (SELECT COUNT(*) FROM documentos WHERE analise_id = NEW.analise_id
                    AND id IN (NEW.documento_1_id, NEW.documento_2_id)) <> 2
                OR ((SELECT modo FROM analises WHERE id = NEW.analise_id) = 'FAMILIAR'
                    AND (SELECT COUNT(*) FROM documentos_pessoas v JOIN documentos d
                         ON d.id = v.documento_id WHERE v.analise_id = NEW.analise_id
                         AND v.pessoa_id = NEW.pessoa_id AND d.escopo = 'INDIVIDUAL'
                         AND v.documento_id IN (NEW.documento_1_id, NEW.documento_2_id)) <> 2)
                OR ((SELECT modo FROM analises WHERE id = NEW.analise_id) = 'INDIVIDUAL'
                    AND NEW.pessoa_id IS NOT NULL)
          ))
        BEGIN SELECT RAISE(ABORT, 'Par de conferência incompatível com a análise.'); END"""
        for operacao in ("INSERT", "UPDATE")
    ),
)


_MIGRACAO_2 = (
    """CREATE TABLE itens_documentais (
           id INTEGER PRIMARY KEY AUTOINCREMENT,
           analise_id INTEGER NOT NULL,
           documento_id INTEGER NOT NULL,
           tipo TEXT NOT NULL CHECK (length(trim(tipo)) > 0),
           ordem INTEGER NOT NULL CHECK (typeof(ordem) = 'integer' AND ordem > 0),
           pessoa_id INTEGER,
           estado_revisao TEXT NOT NULL DEFAULT 'PENDENTE'
               CHECK (estado_revisao IN ('PENDENTE', 'REVISADO')),
           UNIQUE (analise_id, documento_id, ordem),
           FOREIGN KEY (analise_id, documento_id)
               REFERENCES documentos(analise_id, id) ON DELETE CASCADE,
           FOREIGN KEY (analise_id, documento_id, pessoa_id)
               REFERENCES documentos_pessoas(analise_id, documento_id, pessoa_id)
       )""",
    """CREATE INDEX idx_itens_documentais_pessoa ON itens_documentais(analise_id, pessoa_id)
       WHERE pessoa_id IS NOT NULL""",
    """CREATE INDEX idx_itens_documentais_vinculo
       ON itens_documentais(analise_id, documento_id, pessoa_id)
       WHERE pessoa_id IS NOT NULL""",
    """CREATE TABLE campos_item (
           item_id INTEGER NOT NULL REFERENCES itens_documentais(id) ON DELETE CASCADE,
           nome_campo TEXT NOT NULL CHECK (length(trim(nome_campo)) > 0),
           valor TEXT,
           status TEXT NOT NULL DEFAULT 'ENCONTRADO'
               CHECK (status IN ('ENCONTRADO', 'NAO_ENCONTRADO', 'NAO_APLICAVEL',
                                 'AMBIGUO', 'INVALIDO', 'EVIDENCIA_INSUFICIENTE')),
           pagina_origem INTEGER CHECK (pagina_origem IS NULL OR
               (typeof(pagina_origem) = 'integer' AND pagina_origem > 0)),
           trecho_origem TEXT CHECK (trecho_origem IS NULL OR
               (typeof(trecho_origem) = 'text' AND instr(trecho_origem, char(0)) = 0
                AND length(trecho_origem) <= 160)),
           PRIMARY KEY (item_id, nome_campo)
       )""",
    """CREATE TRIGGER itens_documentais_origem_fixa
       BEFORE UPDATE OF id, analise_id, documento_id ON itens_documentais
       WHEN NEW.id <> OLD.id OR NEW.analise_id <> OLD.analise_id
            OR NEW.documento_id <> OLD.documento_id
       BEGIN SELECT RAISE(ABORT, 'A origem do item documental é fixa.'); END""",
    """CREATE TRIGGER campos_item_origem_fixa BEFORE UPDATE OF item_id ON campos_item
       WHEN NEW.item_id <> OLD.item_id
       BEGIN SELECT RAISE(ABORT, 'O item de origem do campo é fixo.'); END""",
    # O vínculo removido precisa liberar apenas a referência à pessoa.
    """CREATE TRIGGER documentos_pessoas_liberar_itens BEFORE DELETE ON documentos_pessoas
       BEGIN
           UPDATE itens_documentais SET pessoa_id = NULL, estado_revisao = 'PENDENTE'
           WHERE analise_id = OLD.analise_id AND documento_id = OLD.documento_id
             AND pessoa_id = OLD.pessoa_id;
       END""",
)

_MIGRACOES = {1: _MIGRACAO_1, 2: _MIGRACAO_2}
