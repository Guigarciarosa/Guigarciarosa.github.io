"""SQLite em modo WAL: esquema, sementes e acesso.

Regra central: produtos idênticos em lojas diferentes compartilham o MESMO id_produto
(produtos_mestres). Só os registros de preço (historico_precos) variam por loja.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import config

ESQUEMA = """
CREATE TABLE IF NOT EXISTS lojas (
    id_loja               INTEGER PRIMARY KEY,
    slug                  TEXT NOT NULL UNIQUE,
    nome                  TEXT NOT NULL,
    url_base              TEXT NOT NULL,
    tipo                  TEXT NOT NULL CHECK (tipo IN ('supermercado','marketplace')),
    plataforma            TEXT NOT NULL,            -- vtex | instabuy | irani | festval | amazon | mercadolivre | shopee
    taxa_entrega_estimada NUMERIC NOT NULL DEFAULT 0,
    pedido_minimo         NUMERIC NOT NULL DEFAULT 0,
    parametros            TEXT NOT NULL DEFAULT '{}', -- JSON por plataforma (sc, subdomínio, filial...)
    ativo                 INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS produtos_mestres (
    id_produto        INTEGER PRIMARY KEY,
    ean_gtin          TEXT,
    nome_normalizado  TEXT NOT NULL,
    nome_exibicao     TEXT NOT NULL,
    marca             TEXT,
    quantidade        REAL,        -- valor na unidade-base (g, ml, un)
    unidade           TEXT,        -- g | ml | un | m
    categoria         TEXT,
    imagem_local_path TEXT,
    criado_em         TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_prod_ean ON produtos_mestres(ean_gtin) WHERE ean_gtin IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_prod_unidade ON produtos_mestres(unidade, quantidade);

-- Vínculo SKU da loja -> produto canônico (acelera coletas seguintes e reconstrói carrinhos)
CREATE TABLE IF NOT EXISTS produto_loja (
    id_loja     INTEGER NOT NULL REFERENCES lojas(id_loja),
    sku_loja    TEXT NOT NULL,
    id_produto  INTEGER NOT NULL REFERENCES produtos_mestres(id_produto),
    seller      TEXT,
    nome_loja   TEXT,
    url         TEXT,
    metodo_match TEXT NOT NULL,     -- novo | rede | ean | fuzzy | manual
    score       REAL,
    chave_cruzada TEXT,             -- id da rede igual entre filiais (ex.: mercafacil:<modelId>)
    PRIMARY KEY (id_loja, sku_loja)
);
CREATE INDEX IF NOT EXISTS ix_pl_produto ON produto_loja(id_produto);
CREATE INDEX IF NOT EXISTS ix_pl_chave ON produto_loja(chave_cruzada) WHERE chave_cruzada IS NOT NULL;

CREATE TABLE IF NOT EXISTS historico_precos (
    id_registro        INTEGER PRIMARY KEY,
    id_produto         INTEGER NOT NULL REFERENCES produtos_mestres(id_produto),
    id_loja            INTEGER NOT NULL REFERENCES lojas(id_loja),
    preco              NUMERIC NOT NULL,
    preco_promocional  NUMERIC,
    disponivel         INTEGER NOT NULL DEFAULT 1,
    url_produto_loja   TEXT,
    sku_loja           TEXT,
    data_coleta        TEXT NOT NULL,   -- quando este preço foi visto pela 1ª vez
    ultima_verificacao TEXT NOT NULL    -- última coleta que confirmou o mesmo preço
);
CREATE INDEX IF NOT EXISTS ix_hist_prod_loja ON historico_precos(id_produto, id_loja, id_registro DESC);
CREATE INDEX IF NOT EXISTS ix_hist_loja ON historico_precos(id_loja);

CREATE TABLE IF NOT EXISTS lista_monitoramento_marketplace (
    id                   INTEGER PRIMARY KEY,
    termo_busca          TEXT NOT NULL,
    id_produto_vinculado INTEGER REFERENCES produtos_mestres(id_produto),
    ativo                INTEGER NOT NULL DEFAULT 1,
    criado_em            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resultados_marketplace (
    id            INTEGER PRIMARY KEY,
    id_monitor    INTEGER NOT NULL REFERENCES lista_monitoramento_marketplace(id) ON DELETE CASCADE,
    id_loja       INTEGER NOT NULL REFERENCES lojas(id_loja),
    titulo        TEXT NOT NULL,
    preco         NUMERIC NOT NULL,
    url           TEXT NOT NULL,
    imagem_url    TEXT,
    score         REAL,
    coletado_em   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_res_monitor ON resultados_marketplace(id_monitor, coletado_em DESC);

CREATE TABLE IF NOT EXISTS credenciais_lojas (
    id_loja        INTEGER PRIMARY KEY REFERENCES lojas(id_loja),
    usuario_cifrado TEXT,
    senha_cifrada   TEXT,
    cookies_cifrados TEXT,
    atualizado_em  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS carrinho (
    id_produto INTEGER PRIMARY KEY REFERENCES produtos_mestres(id_produto),
    quantidade INTEGER NOT NULL CHECK (quantidade > 0)
);

CREATE TABLE IF NOT EXISTS execucoes (
    id            INTEGER PRIMARY KEY,
    id_loja       INTEGER REFERENCES lojas(id_loja),
    inicio        TEXT NOT NULL,
    fim           TEXT,
    status        TEXT NOT NULL,     -- rodando | ok | erro | parcial
    lidos         INTEGER DEFAULT 0,
    total         INTEGER DEFAULT 0,
    novos         INTEGER DEFAULT 0,
    unificados    INTEGER DEFAULT 0,
    precos_atualizados INTEGER DEFAULT 0,
    erros         INTEGER DEFAULT 0,
    bloqueios     INTEGER DEFAULT 0,
    mensagem      TEXT
);

CREATE TABLE IF NOT EXISTS logs (
    id      INTEGER PRIMARY KEY,
    ts      TEXT NOT NULL,
    nivel   TEXT NOT NULL,
    origem  TEXT,
    mensagem TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_logs_nivel ON logs(nivel, id DESC);

-- Último preço por produto e loja (o "agora" do comparador). Tabela materializada,
-- mantida por registrar_preco(), para o catálogo não varrer a série histórica inteira.
CREATE TABLE IF NOT EXISTS precos_atuais (
    id_produto        INTEGER NOT NULL REFERENCES produtos_mestres(id_produto),
    id_loja           INTEGER NOT NULL REFERENCES lojas(id_loja),
    id_registro       INTEGER NOT NULL,
    preco             NUMERIC NOT NULL,
    preco_promocional NUMERIC,
    preco_efetivo     NUMERIC NOT NULL,
    disponivel        INTEGER NOT NULL,
    url_produto_loja  TEXT,
    sku_loja          TEXT,
    data_coleta       TEXT NOT NULL,
    ultima_verificacao TEXT NOT NULL,
    PRIMARY KEY (id_produto, id_loja)
);
CREATE INDEX IF NOT EXISTS ix_atuais_loja ON precos_atuais(id_loja);
"""

# Lojas de Cascavel-PR. Parâmetros por plataforma podem ser ajustados no painel.
LOJAS_SEMENTE: list[dict[str, Any]] = [
    dict(slug="muffato", nome="Super Muffato", url_base="https://www.supermuffato.com.br",
         tipo="supermercado", plataforma="vtex", taxa=9.90, parametros={"sc": "22"}),
    dict(slug="muffatao", nome="Muffatão", url_base="https://muffataosupermercado.instabuy.app.br",
         tipo="supermercado", plataforma="instabuy", taxa=9.90, parametros={"subdominio": "muffataosupermercado"},
         ativo=0),
    dict(slug="irani-parque-verde", nome="Irani Parque Verde", url_base="https://www.irani.delivery/parque-verde",
         tipo="supermercado", plataforma="irani", taxa=7.00, parametros={"filial": "parque-verde", "store_id": "2"}),
    dict(slug="irani-pacaembu", nome="Irani Pacaembu", url_base="https://www.irani.delivery/pacaembu",
         tipo="supermercado", plataforma="irani", taxa=7.00, parametros={"filial": "pacaembu", "store_id": "1"}),
    dict(slug="irani-floresta", nome="Irani Floresta", url_base="https://www.irani.delivery/floresta",
         tipo="supermercado", plataforma="irani", taxa=7.00, parametros={"filial": "floresta", "store_id": "3"}),
    dict(slug="irani-alto-alegre", nome="Irani Alto Alegre", url_base="https://www.irani.delivery/alto-alegre",
         tipo="supermercado", plataforma="irani", taxa=7.00, parametros={"filial": "alto-alegre", "store_id": "4"}),
    dict(slug="festval", nome="Festval", url_base="https://www.festval.com",
         tipo="supermercado", plataforma="festval", taxa=9.90, parametros={"sc": "2", "cep": "85801000"}),
    dict(slug="amazon", nome="Amazon", url_base="https://www.amazon.com.br",
         tipo="marketplace", plataforma="amazon", taxa=0, parametros={}),
    dict(slug="mercadolivre", nome="Mercado Livre", url_base="https://www.mercadolivre.com.br",
         tipo="marketplace", plataforma="mercadolivre", taxa=0, parametros={}),
    dict(slug="shopee", nome="Shopee", url_base="https://shopee.com.br",
         tipo="marketplace", plataforma="shopee", taxa=0, parametros={}, ativo=0),
]

_lock_escrita = threading.RLock()


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conectar(caminho: Path | None = None) -> sqlite3.Connection:
    caminho = caminho or config().db_path
    caminho.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(caminho, timeout=30, check_same_thread=False, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=30000")
    return con


@contextmanager
def transacao(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Transação explícita, serializada entre threads (SQLite tem um escritor por vez)."""
    with _lock_escrita:
        con.execute("BEGIN IMMEDIATE")
        try:
            yield con
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise


def inicializar(con: sqlite3.Connection | None = None) -> sqlite3.Connection:
    con = con or conectar()
    con.executescript(ESQUEMA)
    with transacao(con):
        for l in LOJAS_SEMENTE:
            con.execute(
                """INSERT INTO lojas (slug, nome, url_base, tipo, plataforma, taxa_entrega_estimada, parametros, ativo)
                   VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(slug) DO NOTHING""",
                (l["slug"], l["nome"], l["url_base"], l["tipo"], l["plataforma"], l["taxa"],
                 json.dumps(l["parametros"]), l.get("ativo", 1)),
            )
    return con


def loja_por_slug(con: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM lojas WHERE slug=?", (slug,)).fetchone()


def parametros(loja: sqlite3.Row) -> dict[str, Any]:
    try:
        return json.loads(loja["parametros"] or "{}")
    except (TypeError, ValueError):
        return {}


def registrar_preco(
    con: sqlite3.Connection, id_produto: int, id_loja: int, preco: float,
    preco_promocional: float | None, disponivel: bool, url: str | None, sku: str | None,
    quando: str | None = None,
) -> bool:
    """Grava a série temporal sem inflar o banco: só cria linha quando preço/promo/estoque mudam;
    senão atualiza ultima_verificacao. Retorna True se o preço mudou (ou é o primeiro)."""
    quando = quando or agora()
    ult = con.execute(
        """SELECT id_registro, preco, preco_promocional, disponivel FROM historico_precos
           WHERE id_produto=? AND id_loja=? ORDER BY id_registro DESC LIMIT 1""",
        (id_produto, id_loja),
    ).fetchone()
    promo = preco_promocional if preco_promocional and preco_promocional < preco else None
    if ult is not None and _igual(ult["preco"], preco) and _igual(ult["preco_promocional"], promo) \
            and bool(ult["disponivel"]) == bool(disponivel):
        con.execute("UPDATE historico_precos SET ultima_verificacao=?, url_produto_loja=COALESCE(?, url_produto_loja) "
                    "WHERE id_registro=?", (quando, url, ult["id_registro"]))
        con.execute("UPDATE precos_atuais SET ultima_verificacao=?, url_produto_loja=COALESCE(?, url_produto_loja), "
                    "sku_loja=COALESCE(?, sku_loja) WHERE id_produto=? AND id_loja=?",
                    (quando, url, sku, id_produto, id_loja))
        return False
    cur = con.execute(
        """INSERT INTO historico_precos (id_produto, id_loja, preco, preco_promocional, disponivel,
               url_produto_loja, sku_loja, data_coleta, ultima_verificacao)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (id_produto, id_loja, round(preco, 2), round(promo, 2) if promo else None, int(bool(disponivel)),
         url, sku, quando, quando),
    )
    con.execute(
        """INSERT INTO precos_atuais (id_produto, id_loja, id_registro, preco, preco_promocional, preco_efetivo,
               disponivel, url_produto_loja, sku_loja, data_coleta, ultima_verificacao)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id_produto, id_loja) DO UPDATE SET id_registro=excluded.id_registro, preco=excluded.preco,
               preco_promocional=excluded.preco_promocional, preco_efetivo=excluded.preco_efetivo,
               disponivel=excluded.disponivel, url_produto_loja=excluded.url_produto_loja,
               sku_loja=COALESCE(excluded.sku_loja, precos_atuais.sku_loja),
               data_coleta=excluded.data_coleta, ultima_verificacao=excluded.ultima_verificacao""",
        (id_produto, id_loja, cur.lastrowid, round(preco, 2), round(promo, 2) if promo else None,
         round(promo or preco, 2), int(bool(disponivel)), url, sku, quando, quando),
    )
    return True


def _igual(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) < 0.005


def log(con: sqlite3.Connection, nivel: str, origem: str, mensagem: str) -> None:
    with _lock_escrita:
        con.execute("INSERT INTO logs (ts, nivel, origem, mensagem) VALUES (?,?,?,?)",
                    (agora(), nivel, origem, mensagem[:2000]))
