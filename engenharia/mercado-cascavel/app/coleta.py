"""Orquestrador da coleta noturna: roda os coletores, unifica produtos e grava preços."""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from typing import Any

from . import imagens
from .config import config
from .database import agora, parametros, registrar_preco, transacao
from .eventos import barramento
from .http import Bloqueado, ClienteEducado
from .matcher import ItemColetado, Matcher
from .scrapers import fabricar
from .scrapers.base import Contexto
from .scrapers.marketplaces import BUSCADORES, pontuar

LOTE = 150
_rodando: set[str] = set()
_matcher: Matcher | None = None


def rodando() -> set[str]:
    return set(_rodando)


def _matcher_compartilhado(con: sqlite3.Connection) -> Matcher:
    """Um índice só para todas as lojas que coletam em paralelo, senão a loja B não veria
    os produtos que a loja A acabou de criar e duplicaria o catálogo."""
    global _matcher
    if _matcher is None or not _rodando:
        _matcher = Matcher(con, config().limiar_fuzzy)
    return _matcher


async def coletar_loja(con: sqlite3.Connection, slug: str, cliente: ClienteEducado | None = None) -> dict[str, Any]:
    loja = con.execute("SELECT * FROM lojas WHERE slug=?", (slug,)).fetchone()
    if loja is None:
        raise ValueError(f"loja desconhecida: {slug}")
    if slug in _rodando:
        barramento.log("WARNING", slug, "coleta já em andamento; ignorando novo pedido")
        return {"status": "ja_rodando"}
    if loja["tipo"] == "marketplace":
        return await coletar_watchlist(con, [slug], cliente)

    matcher = _matcher_compartilhado(con)
    _rodando.add(slug)
    prog = barramento.p(slug, loja["nome"])
    prog.__dict__.update(status="rodando", lidos=0, total=0, novos=0, unificados=0, precos_atualizados=0,
                         erros=0, bloqueios=0, inicio=agora(), fim=None, mensagem="", total_estimado=True)
    barramento.atualizar(prog)
    with transacao(con):
        id_exec = con.execute("INSERT INTO execucoes (id_loja, inicio, status) VALUES (?,?,?)",
                              (loja["id_loja"], prog.inicio, "rodando")).lastrowid

    def definir_total(total: int, estimado: bool = True) -> None:
        prog.total = max(total, prog.lidos)
        prog.total_estimado = estimado
        barramento.atualizar(prog)

    def bloqueio(msg: str) -> None:
        prog.bloqueios += 1
        barramento.log("WARNING", slug, msg)

    ctx = Contexto(definir_total=definir_total, log=lambda n, m: barramento.log(n, slug, m), bloqueio=bloqueio)
    proprio = cliente is None
    cliente = cliente or ClienteEducado(ao_bloqueio=bloqueio)
    coletor = fabricar(loja, parametros(loja), cliente, ctx)
    barramento.log("INFO", slug, f"coleta iniciada ({coletor.plataforma})")

    lote: list[ItemColetado] = []
    fila_imagens: list[tuple[int, str]] = []
    status = "ok"
    try:
        async for item in coletor.coletar():
            lote.append(item)
            if len(lote) >= LOTE:
                fila_imagens += _gravar_lote(con, matcher, loja["id_loja"], lote, prog)
                lote = []
                barramento.atualizar(prog)
                fila_imagens = await _baixar_imagens(con, cliente, fila_imagens, prog)
        if lote:
            fila_imagens += _gravar_lote(con, matcher, loja["id_loja"], lote, prog)
        await _baixar_imagens(con, cliente, fila_imagens, prog, tudo=True)
        if prog.lidos == 0:
            status = "erro"
            prog.mensagem = "nenhum produto lido (endpoint mudou ou bloqueio?)"
            barramento.log("ERROR", slug, prog.mensagem)
    except Bloqueado as e:
        status = "parcial" if prog.lidos else "erro"
        prog.mensagem = f"bloqueado: {e}"
        barramento.log("ERROR", slug, prog.mensagem)
    except Exception as e:  # noqa: BLE001
        status = "parcial" if prog.lidos else "erro"
        prog.erros += 1
        prog.mensagem = f"{type(e).__name__}: {e}"
        barramento.log("ERROR", slug, prog.mensagem)
    finally:
        if lote and status != "ok":
            try:
                _gravar_lote(con, matcher, loja["id_loja"], lote, prog)
            except Exception:  # noqa: BLE001
                pass
        if proprio:
            await cliente.fechar()
        _rodando.discard(slug)
        prog.status, prog.fim = status, agora()
        if not prog.total_estimado or prog.total < prog.lidos:
            prog.total = max(prog.total, prog.lidos)
        if status == "ok":
            prog.total = prog.lidos
        with transacao(con):
            con.execute(
                """UPDATE execucoes SET fim=?, status=?, lidos=?, total=?, novos=?, unificados=?,
                   precos_atualizados=?, erros=?, bloqueios=?, mensagem=? WHERE id=?""",
                (prog.fim, status, prog.lidos, prog.total, prog.novos, prog.unificados, prog.precos_atualizados,
                 prog.erros, prog.bloqueios, prog.mensagem, id_exec),
            )
        barramento.atualizar(prog)
        barramento.log("INFO" if status == "ok" else "WARNING", slug,
                       f"fim: {status} | lidos {prog.lidos} | novos {prog.novos} | unificados {prog.unificados} "
                       f"| preços alterados {prog.precos_atualizados} | erros {prog.erros} | bloqueios {prog.bloqueios}")
    return prog.como_dict()


def _gravar_lote(con: sqlite3.Connection, matcher: Matcher, id_loja: int, lote: list[ItemColetado],
                 prog: Any) -> list[tuple[int, str]]:
    """Síncrono de propósito: nenhuma outra corrotina escreve no meio da transação."""
    novas_imagens: list[tuple[int, str]] = []
    quando = agora()
    with transacao(con):
        for item in lote:
            try:
                res = matcher.resolver(item, id_loja)
                if res.criado:
                    prog.novos += 1
                    if item.imagem_url:
                        novas_imagens.append((res.id_produto, item.imagem_url))
                elif res.metodo in ("ean", "fuzzy", "rede"):
                    prog.unificados += 1
                if registrar_preco(con, res.id_produto, id_loja, item.preco, item.preco_promocional,
                                   item.disponivel, item.url, item.sku, quando):
                    prog.precos_atualizados += 1
            except Exception as e:  # noqa: BLE001
                prog.erros += 1
                barramento.log("ERROR", prog.slug, f"item {item.sku} '{item.nome[:60]}': {e}")
            prog.lidos += 1
    if prog.total < prog.lidos:
        prog.total = prog.lidos
    return novas_imagens


async def _baixar_imagens(con: sqlite3.Connection, cliente: ClienteEducado, fila: list[tuple[int, str]],
                          prog: Any, tudo: bool = False) -> list[tuple[int, str]]:
    """Baixa até 40 por vez entre lotes (ou todas no fim), 4 em paralelo."""
    alvo, resto = (fila, []) if tudo else (fila[:40], fila[40:])
    sem = asyncio.Semaphore(4)

    async def um(idp: int, url: str) -> None:
        async with sem:
            try:
                await imagens.baixar(con, cliente, idp, url)
            except Exception as e:  # noqa: BLE001
                barramento.log("WARNING", prog.slug, f"imagem {idp}: {e}")

    await asyncio.gather(*(um(i, u) for i, u in alvo))
    return resto


async def coletar_todas(con: sqlite3.Connection, incluir_marketplaces: bool = True) -> list[dict]:
    lojas = [r["slug"] for r in con.execute("SELECT slug FROM lojas WHERE ativo=1 AND tipo='supermercado'")]
    barramento.log("INFO", "agenda", f"coleta geral: {', '.join(lojas)}")
    _matcher_compartilhado(con)
    # Lojas em paralelo (hosts diferentes); dentro de cada loja o cliente é sequencial com jitter.
    # As 4 filiais Irani compartilham o mesmo host: rodam em série para não parecer ataque.
    irani = [s for s in lojas if s.startswith("irani")]
    outras = [s for s in lojas if s not in irani]

    async def serie(slugs: list[str]) -> list[dict]:
        return [await coletar_loja(con, s) for s in slugs]

    tarefas = [coletar_loja(con, s) for s in outras] + ([serie(irani)] if irani else [])
    res: list[Any] = []
    for r in await asyncio.gather(*tarefas, return_exceptions=True):
        res.extend(r if isinstance(r, list) else [r])
    if incluir_marketplaces:
        res.append(await coletar_watchlist(con))
    return [r if isinstance(r, dict) else {"erro": str(r)} for r in res]


async def coletar_watchlist(con: sqlite3.Connection, slugs: list[str] | None = None,
                            cliente: ClienteEducado | None = None, id_monitor: int | None = None) -> dict:
    lojas = {r["slug"]: r for r in con.execute("SELECT * FROM lojas WHERE tipo='marketplace' AND ativo=1")}
    if slugs:
        lojas = {k: v for k, v in lojas.items() if k in slugs}
    q = "SELECT * FROM lista_monitoramento_marketplace WHERE ativo=1"
    termos = con.execute(q + (" AND id=?" if id_monitor else ""), (id_monitor,) if id_monitor else ()).fetchall()
    prog = barramento.p("marketplaces", "Marketplaces (watchlist)")
    prog.__dict__.update(status="rodando", lidos=0, total=len(termos) * len(lojas), erros=0, bloqueios=0,
                         novos=0, unificados=0, precos_atualizados=0, inicio=agora(), total_estimado=False)
    barramento.atualizar(prog)
    proprio = cliente is None
    cliente = cliente or ClienteEducado(ao_bloqueio=lambda m: barramento.log("WARNING", "marketplaces", m))
    try:
        for t in termos:
            for slug, loja in lojas.items():
                try:
                    ofertas = pontuar(t["termo_busca"], await BUSCADORES[slug](cliente, t["termo_busca"]))
                    quando = agora()
                    with transacao(con):
                        for o in ofertas:
                            con.execute(
                                """INSERT INTO resultados_marketplace (id_monitor, id_loja, titulo, preco, url,
                                       imagem_url, score, coletado_em) VALUES (?,?,?,?,?,?,?,?)""",
                                (t["id"], loja["id_loja"], o.titulo, o.preco, o.url, o.imagem_url, o.score, quando))
                        if ofertas and t["id_produto_vinculado"]:
                            melhor = min(ofertas, key=lambda o: o.preco)
                            if registrar_preco(con, t["id_produto_vinculado"], loja["id_loja"], melhor.preco, None,
                                               True, melhor.url, None, quando):
                                prog.precos_atualizados += 1
                    prog.novos += len(ofertas)
                    barramento.log("INFO", slug, f"'{t['termo_busca']}': {len(ofertas)} ofertas")
                except Exception as e:  # noqa: BLE001
                    prog.erros += 1
                    barramento.log("ERROR", slug, f"'{t['termo_busca']}': {type(e).__name__}: {e}")
                prog.lidos += 1
                barramento.atualizar(prog)
    finally:
        if proprio:
            await cliente.fechar()
        prog.status = "ok" if not prog.erros else ("parcial" if prog.novos else "erro")
        prog.fim = agora()
        barramento.atualizar(prog)
    return prog.como_dict()
