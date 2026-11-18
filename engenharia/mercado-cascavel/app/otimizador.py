"""Otimizador de carrinho.

Modo loja única: a loja mais barata para a cesta INTEIRA, já com a taxa de entrega.
Modo dividido: escolhe o CONJUNTO de lojas que minimiza (itens + taxas de entrega).
  Não é só "cada item na loja mais barata": dividir em 4 lojas para economizar R$ 3 em
  um item e pagar R$ 9,90 de frete é prejuízo. Com ~10 lojas, enumerar subconjuntos de
  até MAX_LOJAS é exato e barato (<= 385 combinações).
"""
from __future__ import annotations

import itertools
import sqlite3
from dataclasses import dataclass, field

MAX_LOJAS = 4


@dataclass
class Oferta:
    id_loja: int
    preco: float          # preço efetivo (promo se houver)
    preco_cheio: float
    url: str | None
    sku: str | None
    seller: str | None


@dataclass
class Linha:
    id_produto: int
    nome: str
    quantidade: int
    imagem: str | None
    ofertas: dict[int, Oferta] = field(default_factory=dict)


@dataclass
class LojaInfo:
    id_loja: int
    slug: str
    nome: str
    taxa: float
    minimo: float
    tipo: str


def carregar(con: sqlite3.Connection, incluir_marketplaces: bool = False) -> tuple[list[Linha], dict[int, LojaInfo]]:
    lojas = {
        r["id_loja"]: LojaInfo(r["id_loja"], r["slug"], r["nome"], float(r["taxa_entrega_estimada"] or 0),
                               float(r["pedido_minimo"] or 0), r["tipo"])
        for r in con.execute("SELECT * FROM lojas WHERE ativo=1")
        if incluir_marketplaces or r["tipo"] == "supermercado"
    }
    linhas: dict[int, Linha] = {}
    for r in con.execute(
        """SELECT c.id_produto, c.quantidade, p.nome_exibicao, p.imagem_local_path FROM carrinho c
           JOIN produtos_mestres p USING (id_produto) ORDER BY p.nome_exibicao"""
    ):
        linhas[r["id_produto"]] = Linha(r["id_produto"], r["nome_exibicao"], r["quantidade"], r["imagem_local_path"])
    if not linhas:
        return [], lojas
    marcas = ",".join("?" * len(linhas))
    for r in con.execute(
        f"""SELECT a.id_produto, a.id_loja, a.preco, a.preco_promocional, a.url_produto_loja, a.sku_loja,
                   pl.seller, pl.sku_loja AS sku_vinculo
            FROM precos_atuais a
            LEFT JOIN produto_loja pl ON pl.id_loja=a.id_loja AND pl.sku_loja=a.sku_loja
            WHERE a.disponivel=1 AND a.id_produto IN ({marcas})""",
        list(linhas),
    ):
        if r["id_loja"] not in lojas:
            continue
        efetivo = float(r["preco_promocional"] or r["preco"])
        linhas[r["id_produto"]].ofertas[r["id_loja"]] = Oferta(
            r["id_loja"], efetivo, float(r["preco"]), r["url_produto_loja"], r["sku_loja"], r["seller"])
    return list(linhas.values()), lojas


def loja_unica(linhas: list[Linha], lojas: dict[int, LojaInfo]) -> list[dict]:
    """Ranking de lojas para a cesta inteira. Completas primeiro, depois por total."""
    out = []
    for lj in lojas.values():
        itens = [(l, l.ofertas[lj.id_loja]) for l in linhas if lj.id_loja in l.ofertas]
        if not itens:
            continue
        subtotal = sum(o.preco * l.quantidade for l, o in itens)
        faltam = [l.nome for l in linhas if lj.id_loja not in l.ofertas]
        out.append({
            "id_loja": lj.id_loja, "slug": lj.slug, "loja": lj.nome, "subtotal": round(subtotal, 2),
            "taxa": lj.taxa, "total": round(subtotal + lj.taxa, 2), "completa": not faltam, "faltam": faltam,
            "abaixo_minimo": subtotal < lj.minimo,
            "itens": [_item(l, o) for l, o in itens],
        })
    out.sort(key=lambda x: (not x["completa"], len(x["faltam"]), x["total"]))
    return out


def dividido(linhas: list[Linha], lojas: dict[int, LojaInfo], max_lojas: int = MAX_LOJAS) -> dict | None:
    cobriveis = [l for l in linhas if l.ofertas]
    if not cobriveis:
        return None
    ids = sorted({i for l in cobriveis for i in l.ofertas})
    melhor: tuple[float, tuple[int, ...], dict[int, int]] | None = None
    for k in range(1, min(max_lojas, len(ids)) + 1):
        for combo in itertools.combinations(ids, k):
            atribuicao: dict[int, int] = {}
            ok = True
            for l in cobriveis:
                opcoes = [i for i in combo if i in l.ofertas]
                if not opcoes:
                    ok = False
                    break
                atribuicao[l.id_produto] = min(opcoes, key=lambda i: l.ofertas[i].preco)
            if not ok:
                continue
            usadas = set(atribuicao.values())
            if len(usadas) < len(combo):
                continue  # combo com loja ociosa é igual a um combo menor já avaliado
            subtot = {i: 0.0 for i in usadas}
            for l in cobriveis:
                i = atribuicao[l.id_produto]
                subtot[i] += l.ofertas[i].preco * l.quantidade
            if any(subtot[i] < lojas[i].minimo for i in usadas):
                continue
            custo = sum(subtot.values()) + sum(lojas[i].taxa for i in usadas)
            if melhor is None or custo < melhor[0] - 0.004:
                melhor = (custo, combo, atribuicao)
    if melhor is None:
        return None
    custo, _combo, atrib = melhor
    grupos: dict[int, dict] = {}
    for l in cobriveis:
        i = atrib[l.id_produto]
        g = grupos.setdefault(i, {"id_loja": i, "slug": lojas[i].slug, "loja": lojas[i].nome,
                                  "taxa": lojas[i].taxa, "subtotal": 0.0, "itens": []})
        o = l.ofertas[i]
        g["subtotal"] += o.preco * l.quantidade
        g["itens"].append(_item(l, o, l.ofertas))
    for g in grupos.values():
        g["subtotal"] = round(g["subtotal"], 2)
        g["total"] = round(g["subtotal"] + g["taxa"], 2)
    # referência ingênua: cada item no mais barato, sem pensar em frete
    ingenuo_itens = sum(min(o.preco for o in l.ofertas.values()) * l.quantidade for l in cobriveis)
    ingenuo_lojas = {min(l.ofertas, key=lambda i: l.ofertas[i].preco) for l in cobriveis}
    ingenuo = ingenuo_itens + sum(lojas[i].taxa for i in ingenuo_lojas)
    return {
        "total": round(custo, 2), "grupos": sorted(grupos.values(), key=lambda g: -g["total"]),
        "sem_oferta": [l.nome for l in linhas if not l.ofertas],
        "ingenuo_total": round(ingenuo, 2), "ingenuo_lojas": len(ingenuo_lojas),
    }


def _item(l: Linha, o: Oferta, todas: dict[int, Oferta] | None = None) -> dict:
    d = {"id_produto": l.id_produto, "nome": l.nome, "quantidade": l.quantidade, "imagem": l.imagem,
         "preco": o.preco, "preco_cheio": o.preco_cheio, "total": round(o.preco * l.quantidade, 2),
         "url": o.url, "sku": o.sku, "seller": o.seller}
    if todas and len(todas) > 1:
        precos = sorted(x.preco for x in todas.values())
        d["segunda_melhor"] = precos[1]
    return d


def otimizar(con: sqlite3.Connection, incluir_marketplaces: bool = False) -> dict:
    linhas, lojas = carregar(con, incluir_marketplaces)
    unica = loja_unica(linhas, lojas)
    div = dividido(linhas, lojas)
    melhor_unica = next((u for u in unica if u["completa"]), None)
    economia = None
    if melhor_unica and div and not div["sem_oferta"]:
        economia = round(melhor_unica["total"] - div["total"], 2)
    return {"itens": len(linhas), "loja_unica": unica, "melhor_unica": melhor_unica, "dividido": div,
            "economia": economia}
