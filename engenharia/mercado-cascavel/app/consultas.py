"""Consultas de leitura usadas pelas telas."""
from __future__ import annotations

import sqlite3
from typing import Any

from .normalizar import sem_acento

POR_PAGINA = 48


def catalogo(con: sqlite3.Connection, q: str = "", categoria: str = "", so_promo: bool = False,
             loja: str = "", ordem: str = "relevancia", pagina: int = 1) -> dict[str, Any]:
    filtros, args = ["a.disponivel=1"], []
    for termo in sem_acento(q).lower().split():
        filtros.append("(p.nome_normalizado LIKE ? OR p.ean_gtin = ?)")
        args += [f"%{termo}%", termo]
    if categoria:
        filtros.append("p.categoria = ?")
        args.append(categoria)
    if so_promo:
        filtros.append("EXISTS (SELECT 1 FROM precos_atuais x WHERE x.id_produto=p.id_produto "
                       "AND x.preco_promocional IS NOT NULL AND x.disponivel=1)")
    if loja:
        filtros.append("EXISTS (SELECT 1 FROM precos_atuais x JOIN lojas lx USING(id_loja) "
                       "WHERE x.id_produto=p.id_produto AND lx.slug=? AND x.disponivel=1)")
        args.append(loja)
    ordens = {
        "relevancia": "n_lojas DESC, p.nome_exibicao",
        "menor_preco": "menor ASC",
        "maior_diferenca": "(maior - menor) DESC",
        "nome": "p.nome_exibicao",
    }
    where = " AND ".join(filtros)
    base = f"""FROM produtos_mestres p JOIN precos_atuais a ON a.id_produto=p.id_produto
               JOIN lojas l ON l.id_loja=a.id_loja AND l.ativo=1 WHERE {where}"""
    total = con.execute(f"SELECT COUNT(DISTINCT p.id_produto) {base}", args).fetchone()[0]
    pagina = max(1, pagina)
    linhas = con.execute(
        f"""SELECT p.id_produto, p.nome_exibicao, p.marca, p.categoria, p.imagem_local_path, p.ean_gtin,
                   MIN(a.preco_efetivo) AS menor, MAX(a.preco_efetivo) AS maior, COUNT(*) AS n_lojas
            {base} GROUP BY p.id_produto ORDER BY {ordens.get(ordem, ordens['relevancia'])}
            LIMIT ? OFFSET ?""",
        args + [POR_PAGINA, (pagina - 1) * POR_PAGINA],
    ).fetchall()
    ids = [r["id_produto"] for r in linhas]
    precos = precos_por_produto(con, ids)
    itens = [dict(r) | {"precos": precos.get(r["id_produto"], [])} for r in linhas]
    return {"itens": itens, "total": total, "pagina": pagina, "paginas": max(1, -(-total // POR_PAGINA))}


def precos_por_produto(con: sqlite3.Connection, ids: list[int]) -> dict[int, list[dict]]:
    if not ids:
        return {}
    out: dict[int, list[dict]] = {}
    for r in con.execute(
        f"""SELECT a.id_produto, a.preco, a.preco_promocional, a.preco_efetivo, a.disponivel, a.url_produto_loja,
                   a.ultima_verificacao, l.id_loja, l.nome AS loja, l.slug, l.tipo, l.taxa_entrega_estimada AS taxa
            FROM precos_atuais a JOIN lojas l USING (id_loja)
            WHERE l.ativo=1 AND a.id_produto IN ({','.join('?' * len(ids))})
            ORDER BY a.disponivel DESC, a.preco_efetivo ASC""",
        ids,
    ):
        out.setdefault(r["id_produto"], []).append(dict(r))
    for lista in out.values():
        disp = [p for p in lista if p["disponivel"]]
        if disp:
            menor = disp[0]["preco_efetivo"]
            for p in lista:
                p["menor"] = bool(p["disponivel"]) and abs(p["preco_efetivo"] - menor) < 0.005
    return out


def produto(con: sqlite3.Connection, id_produto: int) -> dict | None:
    p = con.execute("SELECT * FROM produtos_mestres WHERE id_produto=?", (id_produto,)).fetchone()
    if not p:
        return None
    hist = con.execute(
        """SELECT h.preco, h.preco_promocional, h.disponivel, h.data_coleta, h.ultima_verificacao, l.nome AS loja,
                  l.slug FROM historico_precos h JOIN lojas l USING (id_loja)
           WHERE h.id_produto=? ORDER BY h.data_coleta""",
        (id_produto,),
    ).fetchall()
    vinculos = con.execute(
        """SELECT pl.*, l.nome AS loja FROM produto_loja pl JOIN lojas l USING (id_loja) WHERE pl.id_produto=?""",
        (id_produto,),
    ).fetchall()
    return {"produto": dict(p), "precos": precos_por_produto(con, [id_produto]).get(id_produto, []),
            "historico": [dict(h) for h in hist], "vinculos": [dict(v) for v in vinculos]}


def categorias(con: sqlite3.Connection) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT categoria FROM produtos_mestres WHERE categoria IS NOT NULL GROUP BY categoria "
        "ORDER BY COUNT(*) DESC LIMIT 80")]


def resumo(con: sqlite3.Connection) -> dict:
    um = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "produtos": um("SELECT COUNT(*) FROM produtos_mestres"),
        "unificados": um("SELECT COUNT(*) FROM (SELECT id_produto FROM precos_atuais GROUP BY id_produto "
                         "HAVING COUNT(*) > 1)"),
        "precos": um("SELECT COUNT(*) FROM historico_precos"),
        "com_ean": um("SELECT COUNT(*) FROM produtos_mestres WHERE ean_gtin IS NOT NULL"),
        "carrinho": um("SELECT COALESCE(SUM(quantidade),0) FROM carrinho"),
        "ultima_coleta": um("SELECT MAX(fim) FROM execucoes WHERE status IN ('ok','parcial')"),
    }


def logs(con: sqlite3.Connection, nivel: str = "", origem: str = "", busca: str = "", limite: int = 300) -> list[dict]:
    f, a = [], []
    if nivel:
        niveis = {"INFO": ("INFO", "WARNING", "ERROR"), "WARNING": ("WARNING", "ERROR"), "ERROR": ("ERROR",)}
        sel = niveis.get(nivel.upper(), (nivel.upper(),))
        f.append(f"nivel IN ({','.join('?' * len(sel))})")
        a += list(sel)
    if origem:
        f.append("origem LIKE ?")
        a.append(f"{origem}%")
    if busca:
        f.append("mensagem LIKE ?")
        a.append(f"%{busca}%")
    where = ("WHERE " + " AND ".join(f)) if f else ""
    return [dict(r) for r in con.execute(f"SELECT * FROM logs {where} ORDER BY id DESC LIMIT ?", a + [limite])]


def execucoes(con: sqlite3.Connection, limite: int = 30) -> list[dict]:
    return [dict(r) for r in con.execute(
        """SELECT e.*, l.nome AS loja, l.slug FROM execucoes e LEFT JOIN lojas l USING (id_loja)
           ORDER BY e.id DESC LIMIT ?""", (limite,))]


def watchlist(con: sqlite3.Connection) -> list[dict]:
    out = []
    for w in con.execute(
        """SELECT w.*, p.nome_exibicao AS produto FROM lista_monitoramento_marketplace w
           LEFT JOIN produtos_mestres p ON p.id_produto = w.id_produto_vinculado ORDER BY w.id DESC"""
    ):
        res = con.execute(
            """SELECT r.*, l.nome AS loja, l.slug FROM resultados_marketplace r JOIN lojas l USING (id_loja)
               WHERE r.id_monitor=? AND r.coletado_em = (SELECT MAX(coletado_em) FROM resultados_marketplace x
                     WHERE x.id_monitor=r.id_monitor AND x.id_loja=r.id_loja)
               ORDER BY r.preco ASC""", (w["id"],)).fetchall()
        out.append(dict(w) | {"resultados": [dict(r) for r in res]})
    return out
