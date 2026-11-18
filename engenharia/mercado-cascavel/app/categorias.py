"""Árvore de categorias curada (categoria > subcategoria), independente da bagunça de cada loja.

Cada produto é classificado por regras de palavra sobre o nome normalizado + a categoria que a
loja informou. Para acrescentar categorias, edite TAXONOMIA: a reclassificação roda sozinha
quando o app sobe (ou: python -m app reclassificar).

Regras:
  incluir  = qualquer um destes termos (palavra inteira, sem acento) liga a regra
  excluir  = qualquer um destes termos desliga a regra
  sinonimos = palavras extras que a busca de texto passa a encontrar
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from .database import transacao
from .normalizar import sem_acento


@dataclass
class Regra:
    nome: str
    incluir: list[str]
    excluir: list[str] = field(default_factory=list)
    sinonimos: list[str] = field(default_factory=list)


@dataclass
class Categoria(Regra):
    subcategorias: list[Regra] = field(default_factory=list)


TAXONOMIA: list[Categoria] = [
    Categoria(
        "Vinho",
        incluir=["vinho", "vinhos", "wine", "espumante", "frisante", "malbec", "cabernet", "merlot", "carmenere",
                 "tannat", "sauvignon", "chardonnay", "pinot", "syrah", "tempranillo", "moscatel"],
        excluir=["vinagre", "molho", "tempero", "sobremesa gelatina", "suco", "taca", "tacas", "abridor",
                 "saca rolha", "sacarolha", "rolha", "adega climatizada", "biscoito", "chocolate", "bombom",
                 "shampoo", "sabonete", "desodorante", "hidratante", "vela"],
        sinonimos=["vinhos", "adega"],
        subcategorias=[
            Regra("Argentino", ["argentino", "argentinos", "argentina", "mendoza", "patagonia", "salta", "trapiche", "norton",
                                "catena", "zapata", "el esteco", "alamos", "benjamin", "nieto senetiner",
                                "luigi bosca", "rutini", "zuccardi", "finca las moras", "las moras", "dv catena",
                                "fond de cave", "terrazas", "callia", "colon", "cafayate", "santa julia",
                                "portillo", "elementos", "uxmal"],
                  sinonimos=["argentina", "argentinos"]),
            Regra("Chileno", ["chileno", "chilenos", "chile", "casillero", "concha y toro", "santa helena", "gato negro",
                              "santa carolina", "undurraga", "cono sur", "montes", "reservado", "frontera",
                              "santa rita", "120", "valle central", "maipo", "colchagua", "casablanca",
                              "carmen", "errazuriz", "marques de casa concha", "diablo", "misiones de rengo",
                              "tarapaca", "la playa", "san pedro", "35 sur"],
                  sinonimos=["chile", "chilenos"]),
            Regra("Uruguaio", ["uruguaio", "uruguaios", "uruguai", "uruguay", "canelones", "pisano", "garzon", "juanico",
                               "deicas", "don pascual", "bouza", "familia deicas", "castillo viejo", "marichal",
                               "bodega garzon", "tannat"],
                  sinonimos=["uruguai", "uruguaios"]),
            Regra("Português", ["portugues", "portugueses", "portugal", "douro", "alentejo", "alentejano", "dao", "vinho verde",
                                "verde", "porto", "periquita", "casal garcia", "gazela", "mateus", "esporao",
                                "porca de murca", "monte velho", "quinta", "papa figos", "cartuxa", "aveleda",
                                "borba", "lisboa", "tejo", "setubal", "bairrada", "trinca bolotas"],
                  sinonimos=["portugal", "portugueses"]),
        ],
    ),
    Categoria(
        "Limpeza",
        incluir=["limpeza", "detergente", "lava loucas", "lava louca", "lava roupa", "lava roupas", "sabao", "omo", "ariel",
                 "brilhante", "tixan", "surf", "ace", "ype", "limpol", "minuano", "invicto", "bem te vi"],
        excluir=["sabonete", "shampoo", "condicionador", "maquina de lavar", "escova", "pano", "esponja",
                 "pele", "facial", "rosto", "demaquilante", "lenco umedecido", "limpeza de pele"],
        sinonimos=["produtos de limpeza"],
        subcategorias=[
            Regra("Detergente", ["detergente", "lava loucas", "lava louca", "limpol"],
                  excluir=["lava roupa", "lava roupas", "roupa", "roupas", "sabao em po", "maquina", "amaciante"],
                  sinonimos=["detergentes", "lava loucas"]),
            Regra("Sabão de lavar roupa", ["lava roupa", "lava roupas", "sabao em po", "sabao po", "sabao liquido",
                                           "sabao em barra", "sabao barra", "omo", "ariel", "brilhante", "tixan",
                                           "surf", "ace", "roupas", "roupa"],
                  excluir=["amaciante", "alvejante", "tira manchas", "detergente", "lava loucas"],
                  sinonimos=["sabao", "sabao em po", "lava roupa", "roupa"]),
        ],
    ),
]


def _texto(*partes: str | None) -> str:
    t = sem_acento(" ".join(p for p in partes if p)).lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return f" {t.strip()} "


def _bate(texto: str, termos: list[str]) -> bool:
    return any(f" {t} " in texto for t in termos)


def _aplica(regra: Regra, texto: str) -> bool:
    return _bate(texto, regra.incluir) and not _bate(texto, regra.excluir)


def classificar(nome: str, categoria_loja: str | None = None, marca: str | None = None) -> tuple[str | None, str | None]:
    """Devolve (categoria, subcategoria). A categoria da loja ajuda (ex.: 'Vinhos Argentinos')."""
    texto = _texto(nome, categoria_loja, marca)
    for cat in TAXONOMIA:
        if not _aplica(cat, texto):
            continue
        # sub pelo nome primeiro (mais confiável), depois nome + categoria da loja
        for alvo in (_texto(nome, marca), texto):
            for sub in cat.subcategorias:
                if _aplica(sub, alvo):
                    return cat.nome, sub.nome
        return cat.nome, None
    return None, None


def tags_busca(categoria: str | None, subcategoria: str | None) -> str | None:
    """Texto extra indexado na busca: 'vinho chileno chile chilenos vinhos adega'."""
    if not categoria:
        return None
    partes = [categoria]
    for cat in TAXONOMIA:
        if cat.nome == categoria:
            partes += cat.sinonimos
            for sub in cat.subcategorias:
                if sub.nome == subcategoria:
                    partes += [sub.nome, *sub.sinonimos]
    return _texto(*partes).strip()


def arvore() -> list[dict]:
    return [{"nome": c.nome, "subs": [s.nome for s in c.subcategorias]} for c in TAXONOMIA]


def reclassificar(con: sqlite3.Connection) -> int:
    """Reaplica as regras a todo o catálogo. Rápido (só regex em memória); roda no boot."""
    linhas = con.execute("SELECT id_produto, nome_exibicao, categoria, marca, categoria_curada, subcategoria "
                         "FROM produtos_mestres").fetchall()
    mudancas = []
    for r in linhas:
        cat, sub = classificar(r["nome_exibicao"], r["categoria"], r["marca"])
        if (cat, sub) != (r["categoria_curada"], r["subcategoria"]):
            mudancas.append((cat, sub, tags_busca(cat, sub), r["id_produto"]))
    if mudancas:
        with transacao(con):
            con.executemany("UPDATE produtos_mestres SET categoria_curada=?, subcategoria=?, tags_busca=? "
                            "WHERE id_produto=?", mudancas)
    return len(mudancas)
