"""Motor de resolução de entidades (deduplicação entre lojas).

Ordem de decisão para cada item coletado:
  0. SKU já vinculado nesta loja (produto_loja)  -> reaproveita id_produto
     0b. chave cruzada da rede (ex.: modelId Mercafácil igual nas 4 filiais Irani)
  1. EAN/GTIN idêntico                            -> mesmo id_produto
  2. Fuzzy (RapidFuzz: média de token_sort_ratio e token_set_ratio >= LIMIAR) dentro do MESMO bloco de medida
     (pacote, valor, unidade iguais: 500g != 1kg != 2x500g), com travas:
       - EANs diferentes nos dois lados nunca unificam
       - marcas diferentes nunca unificam
       - números e descritores (zero, integral, limão...) precisam bater
  3. Senão                                         -> cria produto novo
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from . import normalizar as nz
from .database import agora


@dataclass
class ItemColetado:
    sku: str
    nome: str
    preco: float
    marca: str | None = None
    ean: str | None = None
    preco_promocional: float | None = None
    disponivel: bool = True
    categoria: str | None = None
    imagem_url: str | None = None
    url: str | None = None
    seller: str | None = None
    chave_cruzada: str | None = None   # id estável entre filiais da mesma rede (ex.: mercafacil:<modelId>)
    extra: dict = field(default_factory=dict)


@dataclass
class Resultado:
    id_produto: int
    metodo: str           # sku | rede | ean | fuzzy | novo
    score: float | None = None
    criado: bool = False


@dataclass
class _Candidato:
    id_produto: int
    corpo: str
    marca: str | None
    ean: str | None
    tokens: frozenset


def pontuacao(a: str, b: str, **_: object) -> float:
    """token_set perdoa palavras extras ("torrado e moído"); token_sort pune a falta delas.
    A média equilibra os dois; as travas de medida/marca/descritor cuidam do resto."""
    return (fuzz.token_sort_ratio(a, b) + fuzz.token_set_ratio(a, b)) / 2


def _chave_bloco(m: nz.Medida) -> tuple:
    return (m.pacote, m.valor, m.unidade)


class Matcher:
    """Mantém um índice em memória por bloco de medida; carregue uma vez por coleta."""

    def __init__(self, con: sqlite3.Connection, limiar: float = 88.0):
        self.con = con
        self.limiar = limiar
        self._por_ean: dict[str, int] = {}
        self._por_chave: dict[str, int] = {}
        self._blocos: dict[tuple, list[_Candidato]] = defaultdict(list)
        self._carregar()

    def _carregar(self) -> None:
        for r in self.con.execute("SELECT chave_cruzada, id_produto FROM produto_loja WHERE chave_cruzada IS NOT NULL"):
            self._por_chave[r["chave_cruzada"]] = r["id_produto"]
        for r in self.con.execute(
            "SELECT id_produto, ean_gtin, nome_exibicao, marca FROM produtos_mestres"
        ):
            self._indexar(r["id_produto"], r["nome_exibicao"], r["marca"], r["ean_gtin"])

    def _indexar(self, id_produto: int, nome: str, marca: str | None, ean: str | None) -> None:
        if ean:
            self._por_ean[ean] = id_produto
        toks = nz.tokens_nome(nome)
        marca_n = " ".join(nz.tokens_nome(marca)) if marca else None
        corpo = " ".join(sorted(set(toks)))
        self._blocos[_chave_bloco(nz.extrair_medida(nome))].append(
            _Candidato(id_produto, corpo, marca_n, ean, frozenset(toks))
        )

    # ------------------------------------------------------------------ API
    def resolver(self, item: ItemColetado, id_loja: int) -> Resultado:
        ean = nz.normalizar_ean(item.ean)

        r = self.con.execute(
            "SELECT id_produto FROM produto_loja WHERE id_loja=? AND sku_loja=?", (id_loja, item.sku)
        ).fetchone()
        if r:
            return Resultado(r["id_produto"], "sku")

        if item.chave_cruzada and item.chave_cruzada in self._por_chave:
            res = Resultado(self._por_chave[item.chave_cruzada], "rede", 100.0)
            self._vincular(item, id_loja, res)
            return res

        if ean and ean in self._por_ean:
            res = Resultado(self._por_ean[ean], "ean", 100.0)
            self._vincular(item, id_loja, res)
            return res

        achado = self.melhor_fuzzy(item.nome, item.marca, ean)
        if achado:
            res = Resultado(achado[0], "fuzzy", achado[1])
            if ean:  # produto canônico sem EAN ganha o EAN agora
                self.con.execute(
                    "UPDATE produtos_mestres SET ean_gtin=? WHERE id_produto=? AND ean_gtin IS NULL",
                    (ean, achado[0]),
                )
                self._por_ean[ean] = achado[0]
            self._vincular(item, id_loja, res)
            return res

        id_novo = self._criar(item, ean)
        res = Resultado(id_novo, "novo", None, criado=True)
        self._vincular(item, id_loja, res)
        return res

    def melhor_fuzzy(self, nome: str, marca: str | None, ean: str | None) -> tuple[int, float] | None:
        bloco = self._blocos.get(_chave_bloco(nz.extrair_medida(nome)))
        if not bloco:
            return None
        toks = nz.tokens_nome(nome)
        corpo = " ".join(sorted(set(toks)))
        marca_n = " ".join(nz.tokens_nome(marca)) if marca else None
        escolhas = [c.corpo for c in bloco]
        # top-5 acima do limiar e então aplica as travas, do maior score para o menor
        for _txt, score, idx in process.extract(
            corpo, escolhas, scorer=pontuacao, score_cutoff=self.limiar, limit=5
        ):
            c = bloco[idx]
            if ean and c.ean and ean != c.ean:
                continue
            if marca_n and c.marca and fuzz.ratio(marca_n, c.marca) < 85:
                continue
            if nz.numeros(toks) != nz.numeros(c.tokens):
                continue
            if nz.descritores(toks) != nz.descritores(c.tokens):
                continue
            return c.id_produto, float(score)
        return None

    # ------------------------------------------------------------ internos
    def _criar(self, item: ItemColetado, ean: str | None) -> int:
        med = nz.extrair_medida(item.nome)
        cur = self.con.execute(
            """INSERT INTO produtos_mestres (ean_gtin, nome_normalizado, nome_exibicao, marca, quantidade,
                   unidade, categoria, criado_em) VALUES (?,?,?,?,?,?,?,?)""",
            (ean, nz.nome_normalizado(item.nome, item.marca), item.nome.strip(), item.marca,
             med.total, med.unidade, item.categoria, agora()),
        )
        idp = int(cur.lastrowid)
        self._indexar(idp, item.nome, item.marca, ean)
        return idp

    def _vincular(self, item: ItemColetado, id_loja: int, res: Resultado) -> None:
        self.con.execute(
            """INSERT INTO produto_loja (id_loja, sku_loja, id_produto, seller, nome_loja, url, metodo_match, score,
                   chave_cruzada) VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id_loja, sku_loja) DO UPDATE SET url=excluded.url, nome_loja=excluded.nome_loja,
                   seller=excluded.seller""",
            (id_loja, item.sku, res.id_produto, item.seller, item.nome, item.url, res.metodo, res.score,
             item.chave_cruzada),
        )
        if item.chave_cruzada:
            self._por_chave.setdefault(item.chave_cruzada, res.id_produto)
