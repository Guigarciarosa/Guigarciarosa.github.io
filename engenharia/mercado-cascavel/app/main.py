"""API e painel (FastAPI + Jinja2/HTMX/Alpine)."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import cofre, coleta, consultas, otimizador
from .checkout.worker import gerente
from .config import config
from .database import agora, conectar, inicializar, parametros, transacao
from .eventos import barramento
from .scrapers import fabricar
from .scrapers.base import ItemCarrinho

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
RAIZ = Path(__file__).parent
templates = Jinja2Templates(directory=str(RAIZ / "templates"))
_tarefas: set[asyncio.Task] = set()


def brl(v: Any) -> str:
    if v is None:
        return "—"
    s = f"{float(v):,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


templates.env.filters["brl"] = brl


@asynccontextmanager
async def ciclo(app: FastAPI):
    cfg = config()
    cfg.garantir_pastas()
    con = inicializar(conectar())
    app.state.con = con
    barramento.ligar(con, asyncio.get_running_loop())
    if not cfg.painel_senha:
        barramento.log("WARNING", "painel", "PAINEL_SENHA vazia: painel sem login (só use na LAN)")
    if not cfg.fernet_key:
        barramento.log("WARNING", "cofre", "FERNET_KEY vazia: cofre de credenciais desligado")
    agendador = None
    if cfg.agendador:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        agendador = AsyncIOScheduler(timezone="America/Sao_Paulo")
        async def coleta_noturna() -> None:
            # coroutine de propósito: o AsyncIOScheduler roda funções síncronas numa thread sem event loop
            barramento.log("INFO", "agenda", "coleta noturna disparada")
            await coleta.coletar_todas(con)

        agendador.add_job(coleta_noturna, "cron", hour=cfg.cron_hora,
                          minute=cfg.cron_minuto, id="coleta_noturna", max_instances=1, coalesce=True,
                          misfire_grace_time=3600)
        agendador.start()
        barramento.log("INFO", "agenda", f"coleta diária às {cfg.cron_hora:02d}:{cfg.cron_minuto:02d} (America/Sao_Paulo)")
    app.state.agendador = agendador
    yield
    if agendador:
        agendador.shutdown(wait=False)
    await gerente.fechar()
    con.close()


app = FastAPI(title="Mercado Cascavel", lifespan=ciclo)
app.mount("/static", StaticFiles(directory=str(RAIZ / "static")), name="static")


@app.get("/media/produtos/{caminho:path}")
async def midia(caminho: str):
    """Imagens locais (baixadas uma vez). Resolve a pasta a cada pedido e impede path traversal."""
    raiz = config().media_dir.resolve()
    alvo = (raiz / caminho).resolve()
    if raiz not in alvo.parents or not alvo.is_file():
        raise HTTPException(404)
    return FileResponse(alvo, headers={"Cache-Control": "public, max-age=2592000, immutable"})


def _disparar(coro: Any) -> asyncio.Task:
    t = asyncio.ensure_future(coro)
    _tarefas.add(t)
    t.add_done_callback(_tarefas.discard)
    return t


def con(request: Request):
    return request.app.state.con


# ----------------------------------------------------------------- login
COOKIE = "mercado_sessao"


def _segredo() -> bytes:
    cfg = config()
    return hashlib.sha256(("mercado:" + cfg.painel_senha + ":" + cfg.fernet_key).encode()).digest()


def _assinar(ts: int) -> str:
    return f"{ts}.{hmac.new(_segredo(), str(ts).encode(), hashlib.sha256).hexdigest()}"


def _valido(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    ts, _ = token.split(".", 1)
    try:
        ok = hmac.compare_digest(token, _assinar(int(ts)))
        return ok and time.time() - int(ts) < 30 * 86400
    except ValueError:
        return False


@app.middleware("http")
async def exige_login(request: Request, call_next):
    livre = request.url.path.startswith(("/static", "/login", "/saude"))
    if config().painel_senha and not livre and not _valido(request.cookies.get(COOKIE)):
        if request.url.path.startswith("/api") or request.headers.get("HX-Request"):
            return JSONResponse({"erro": "login necessário"}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    return await call_next(request)


@app.get("/login", response_class=HTMLResponse)
async def tela_login(request: Request, erro: str = ""):
    return templates.TemplateResponse(request, "login.html", {"erro": erro})


@app.post("/login")
async def fazer_login(senha: str = Form(...)):
    await asyncio.sleep(0.6)  # freia força bruta
    if not config().painel_senha or not hmac.compare_digest(senha, config().painel_senha):
        return RedirectResponse("/login?erro=1", status_code=303)
    r = RedirectResponse("/", status_code=303)
    r.set_cookie(COOKIE, _assinar(int(time.time())), httponly=True, samesite="lax", max_age=30 * 86400)
    return r


@app.get("/sair")
async def sair():
    r = RedirectResponse("/login", status_code=303)
    r.delete_cookie(COOKIE)
    return r


@app.get("/saude")
async def saude(request: Request):
    con(request).execute("SELECT 1")
    return {"ok": True, "rodando": sorted(coleta.rodando())}


# ----------------------------------------------------------------- catálogo
@app.get("/", response_class=HTMLResponse)
async def inicio(request: Request, q: str = "", categoria: str = "", so_promo: bool = False, loja: str = "",
                 ordem: str = "relevancia", pagina: int = 1):
    c = con(request)
    ctx = {"q": q, "categoria": categoria, "so_promo": so_promo, "loja": loja, "ordem": ordem,
           "cat": consultas.catalogo(c, q, categoria, so_promo, loja, ordem, pagina),
           "categorias": consultas.categorias(c), "resumo": consultas.resumo(c),
           "lojas": [dict(r) for r in c.execute("SELECT * FROM lojas WHERE ativo=1 ORDER BY tipo DESC, nome")],
           "pagina_atual": "catalogo"}
    nome = "parciais/grade.html" if request.headers.get("HX-Request") else "catalogo.html"
    return templates.TemplateResponse(request, nome, ctx)


@app.get("/produto/{id_produto}", response_class=HTMLResponse)
async def tela_produto(request: Request, id_produto: int):
    d = consultas.produto(con(request), id_produto)
    if not d:
        raise HTTPException(404)
    series: dict[str, list] = {}
    for h in d["historico"]:
        series.setdefault(h["loja"], []).append([h["data_coleta"][:10], float(h["preco_promocional"] or h["preco"])])
    return templates.TemplateResponse(request, "produto.html", d | {"series": json.dumps(series),
                                                                     "pagina_atual": "catalogo"})


@app.get("/api/produtos")
async def api_produtos(request: Request, q: str = "", pagina: int = 1):
    return consultas.catalogo(con(request), q, pagina=pagina)


# ----------------------------------------------------------------- carrinho
@app.post("/carrinho/{id_produto}", response_class=HTMLResponse)
async def carrinho_por(request: Request, id_produto: int, quantidade: int = Form(1), somar: bool = Form(True)):
    c = con(request)
    with transacao(c):
        if quantidade <= 0 and not somar:
            c.execute("DELETE FROM carrinho WHERE id_produto=?", (id_produto,))
        else:
            c.execute(
                f"""INSERT INTO carrinho (id_produto, quantidade) VALUES (?, ?)
                   ON CONFLICT(id_produto) DO UPDATE SET quantidade =
                   {'carrinho.quantidade + excluded.quantidade' if somar else 'excluded.quantidade'}""",
                (id_produto, max(1, quantidade)))
            c.execute("DELETE FROM carrinho WHERE quantidade <= 0")
    total = c.execute("SELECT COALESCE(SUM(quantidade),0) FROM carrinho").fetchone()[0]
    return HTMLResponse(str(total), headers={"HX-Trigger": json.dumps({"carrinho": total})})


@app.delete("/carrinho/{id_produto}")
async def carrinho_tirar(request: Request, id_produto: int):
    c = con(request)
    with transacao(c):
        c.execute("DELETE FROM carrinho WHERE id_produto=?", (id_produto,))
    return {"ok": True}


@app.delete("/carrinho")
async def carrinho_limpar(request: Request):
    c = con(request)
    with transacao(c):
        c.execute("DELETE FROM carrinho")
    return {"ok": True}


@app.get("/carrinho", response_class=HTMLResponse)
async def tela_carrinho(request: Request):
    return templates.TemplateResponse(request, "carrinho.html", {"pagina_atual": "carrinho",
                                                                 "resumo": consultas.resumo(con(request))})


@app.get("/api/carrinho")
async def api_carrinho(request: Request, marketplaces: bool = False):
    return otimizador.otimizar(con(request), marketplaces)


def _grupos(c: Any, modo: str, slug: str | None, marketplaces: bool) -> list[dict]:
    plano = otimizador.otimizar(c, marketplaces)
    if modo == "unica":
        alvo = next((u for u in plano["loja_unica"] if u["slug"] == slug), None) if slug else plano["melhor_unica"]
        return [alvo] if alvo else []
    return plano["dividido"]["grupos"] if plano["dividido"] else []


@app.get("/api/checkout/links")
async def checkout_links(request: Request, modo: str = "dividido", slug: str | None = None,
                         marketplaces: bool = False):
    """Plano B sem robô: deep links que montam o carrinho no SEU navegador (VTEX) ou a lista de produtos."""
    c = con(request)
    out = []
    for g in _grupos(c, modo, slug, marketplaces):
        loja = c.execute("SELECT * FROM lojas WHERE slug=?", (g["slug"],)).fetchone()
        col = fabricar(loja, parametros(loja), None, None)
        itens = [ItemCarrinho(i["sku"], i["quantidade"], i.get("seller"), i.get("url"), i["nome"]) for i in g["itens"]]
        out.append({"slug": g["slug"], "loja": g["loja"], "link_carrinho": col.link_carrinho(itens),
                    "produtos": [{"nome": i.nome, "quantidade": i.quantidade, "url": i.url} for i in itens]})
    return out


@app.post("/api/checkout")
async def checkout_iniciar(request: Request, modo: str = Form("dividido"), slug: str | None = Form(None),
                           marketplaces: bool = Form(False)):
    c = con(request)
    grupos = _grupos(c, modo, slug, marketplaces)
    if not grupos:
        raise HTTPException(400, "carrinho vazio ou sem ofertas")
    _disparar(gerente.iniciar(c, grupos))
    barramento.log("INFO", "checkout", f"montando {len(grupos)} carrinho(s): {', '.join(g['loja'] for g in grupos)}"
                                       " — o robô para na revisão/pagamento e não paga")
    return {"ok": True, "lojas": [g["slug"] for g in grupos]}


@app.get("/api/checkout")
async def checkout_estado():
    return gerente.estado()


@app.post("/api/checkout/fechar")
async def checkout_fechar(slug: str | None = Form(None)):
    await gerente.fechar(slug)
    return {"ok": True}


# ----------------------------------------------------------------- coleta / observabilidade
@app.get("/coleta", response_class=HTMLResponse)
async def tela_coleta(request: Request):
    c = con(request)
    lojas = [dict(r) for r in c.execute("SELECT * FROM lojas ORDER BY tipo DESC, nome")]
    prog = {s: p.como_dict() for s, p in barramento.progresso.items()}
    return templates.TemplateResponse(request, "coleta.html", {
        "lojas": lojas, "progresso": prog, "execucoes": consultas.execucoes(c), "pagina_atual": "coleta",
        "logs": consultas.logs(c, limite=200), "resumo": consultas.resumo(c),
        "proxima": _proxima(request)})


def _proxima(request: Request) -> str | None:
    ag = request.app.state.agendador
    job = ag.get_job("coleta_noturna") if ag else None
    return job.next_run_time.strftime("%d/%m %H:%M") if job and job.next_run_time else None


@app.post("/api/sync")
async def sync_tudo(request: Request):
    _disparar(coleta.coletar_todas(con(request)))
    return {"ok": True}


@app.post("/api/sync/{slug}")
async def sync_loja(request: Request, slug: str):
    c = con(request)
    if not c.execute("SELECT 1 FROM lojas WHERE slug=?", (slug,)).fetchone() and slug != "marketplaces":
        raise HTTPException(404)
    _disparar(coleta.coletar_watchlist(c) if slug == "marketplaces" else coleta.coletar_loja(c, slug))
    return {"ok": True}


@app.get("/api/progresso")
async def api_progresso():
    return {s: p.como_dict() for s, p in barramento.progresso.items()}


@app.get("/api/logs")
async def api_logs(request: Request, nivel: str = "", origem: str = "", busca: str = "", limite: int = 300):
    return consultas.logs(con(request), nivel, origem, busca, min(limite, 2000))


@app.get("/api/eventos")
async def eventos(request: Request):
    q = barramento.assinar()

    async def fluxo():
        try:
            yield "retry: 3000\n\n"
            for p in barramento.progresso.values():
                yield f"event: progresso\ndata: {json.dumps(p.como_dict(), ensure_ascii=False)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"event: {ev['tipo']}\ndata: {json.dumps(ev['dados'], ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            barramento.cancelar(q)

    return StreamingResponse(fluxo(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ----------------------------------------------------------------- watchlist
@app.get("/watchlist", response_class=HTMLResponse)
async def tela_watchlist(request: Request):
    return templates.TemplateResponse(request, "watchlist.html", {
        "itens": consultas.watchlist(con(request)), "pagina_atual": "watchlist",
        "resumo": consultas.resumo(con(request))})


@app.post("/watchlist")
async def watchlist_novo(request: Request, termo_busca: str = Form(...), id_produto: str = Form("")):
    c = con(request)
    termo = termo_busca.strip()
    if not termo:
        raise HTTPException(400, "termo vazio")
    idp = int(id_produto) if id_produto.strip().isdigit() else None
    with transacao(c):
        novo = c.execute("INSERT INTO lista_monitoramento_marketplace (termo_busca, id_produto_vinculado, ativo, "
                         "criado_em) VALUES (?,?,1,?)", (termo, idp, agora())).lastrowid
    _disparar(coleta.coletar_watchlist(c, id_monitor=novo))
    return RedirectResponse("/watchlist", status_code=303)


@app.post("/watchlist/{id}/alternar")
async def watchlist_alternar(request: Request, id: int):
    c = con(request)
    with transacao(c):
        c.execute("UPDATE lista_monitoramento_marketplace SET ativo = 1 - ativo WHERE id=?", (id,))
    return RedirectResponse("/watchlist", status_code=303)


@app.post("/watchlist/{id}/buscar")
async def watchlist_buscar(request: Request, id: int):
    _disparar(coleta.coletar_watchlist(con(request), id_monitor=id))
    return {"ok": True}


@app.post("/watchlist/{id}/vincular")
async def watchlist_vincular(request: Request, id: int, id_produto: str = Form("")):
    c = con(request)
    idp = int(id_produto) if id_produto.strip().isdigit() else None
    with transacao(c):
        c.execute("UPDATE lista_monitoramento_marketplace SET id_produto_vinculado=? WHERE id=?", (idp, id))
    return RedirectResponse("/watchlist", status_code=303)


@app.post("/watchlist/{id}/apagar")
async def watchlist_apagar(request: Request, id: int):
    c = con(request)
    with transacao(c):
        c.execute("DELETE FROM resultados_marketplace WHERE id_monitor=?", (id,))
        c.execute("DELETE FROM lista_monitoramento_marketplace WHERE id=?", (id,))
    return RedirectResponse("/watchlist", status_code=303)


# ----------------------------------------------------------------- lojas e cofre
@app.get("/lojas", response_class=HTMLResponse)
async def tela_lojas(request: Request, ok: str = "", erro: str = ""):
    c = con(request)
    lojas = [dict(r) for r in c.execute("SELECT * FROM lojas ORDER BY tipo DESC, nome")]
    return templates.TemplateResponse(request, "lojas.html", {
        "lojas": lojas, "cred": cofre.resumo(c), "cofre_ok": bool(config().fernet_key),
        "ok": ok, "erro": erro, "pagina_atual": "lojas", "resumo": consultas.resumo(c)})


@app.post("/lojas/{id_loja}")
async def lojas_salvar(request: Request, id_loja: int, taxa: float = Form(0), minimo: float = Form(0),
                       ativo: bool = Form(False), parametros_json: str = Form("{}")):
    try:
        params = json.loads(parametros_json or "{}")
        assert isinstance(params, dict)
    except (ValueError, AssertionError):
        return RedirectResponse("/lojas?erro=parametros", status_code=303)
    c = con(request)
    with transacao(c):
        c.execute("UPDATE lojas SET taxa_entrega_estimada=?, pedido_minimo=?, ativo=?, parametros=? WHERE id_loja=?",
                  (taxa, minimo, int(ativo), json.dumps(params), id_loja))
    return RedirectResponse("/lojas?ok=loja", status_code=303)


@app.post("/lojas/{id_loja}/credenciais")
async def lojas_credenciais(request: Request, id_loja: int, usuario: str = Form(""), senha: str = Form(""),
                            cookies: str = Form("")):
    try:
        lista = json.loads(cookies) if cookies.strip() else None
        if lista is not None and not isinstance(lista, list):
            raise ValueError
        cofre.salvar(con(request), id_loja, usuario or None, senha or None, lista)
    except cofre.CofreIndisponivel:
        return RedirectResponse("/lojas?erro=cofre", status_code=303)
    except ValueError:
        return RedirectResponse("/lojas?erro=cookies", status_code=303)
    return RedirectResponse("/lojas?ok=credenciais", status_code=303)


@app.post("/lojas/{id_loja}/credenciais/apagar")
async def lojas_credenciais_apagar(request: Request, id_loja: int):
    cofre.apagar(con(request), id_loja)
    return RedirectResponse("/lojas?ok=apagado", status_code=303)
