"""Checkout multi-loja automatizado (RPA) que PARA na tela de revisão/pagamento.

Garantias de segurança (não negociáveis):
  1. O código não tem nenhum passo que clique em "pagar", "finalizar pedido" ou similar.
  2. Durante a automação, requisições para endpoints de pagamento/fechamento de pedido são
     abortadas pela rota de guarda (`_guarda_pagamento`). A guarda só sai quando o robô termina
     e entrega a aba para o humano, que confirma o pagamento com as próprias mãos.
  3. Cliques automáticos passam por `_clique_seguro`, que recusa qualquer elemento cujo texto
     pareça confirmação de compra.

Cada loja ganha um contexto Playwright PERSISTENTE (browser_profiles/<slug>): faça login uma vez
pelo noVNC e a sessão fica salva. Se houver credenciais no cofre, o robô tenta o login sozinho;
cookies salvos no cofre são injetados antes de abrir a loja.
"""
from __future__ import annotations

import asyncio
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .. import cofre
from ..config import config
from ..database import parametros
from ..eventos import barramento
from ..scrapers import fabricar
from ..scrapers.base import ItemCarrinho

PROIBIDO_CLICAR = re.compile(
    r"(finalizar\s+(pedido|compra|pagamento)|confirmar\s+(pedido|compra|pagamento)|concluir\s+(pedido|compra)|"
    r"\bpagar\b|place\s+order|buy\s+now|comprar\s+agora|fechar\s+pedido|efetuar\s+pagamento)",
    re.I,
)
ROTAS_PAGAMENTO = re.compile(
    r"(/api/checkout/pub/orderForm/[^/]+/transaction|/api/checkout/pub/orders|/gatewayCallback|/api/payments|"
    r"/payment/(process|authorize)|/checkout/(place|confirm)|/pedido/(finalizar|confirmar)|/order/(place|submit))",
    re.I,
)

SELETORES_ADICIONAR = [
    "button:has-text('Adicionar')", "button:has-text('Comprar')", "button:has-text('Add')",
    "[data-testid*='add-to-cart']", "button[class*='add']", "button[aria-label*='dicionar']",
]


@dataclass
class SessaoCheckout:
    slug: str
    nome: str
    status: str = "na_fila"   # na_fila | abrindo | login | montando | pronto | erro
    mensagem: str = ""
    url_final: str | None = None
    itens_ok: int = 0
    itens_total: int = 0
    contexto: Any = None
    alertas: list[str] = field(default_factory=list)

    def como_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "contexto"}


class GerenteCheckout:
    def __init__(self) -> None:
        self.sessoes: dict[str, SessaoCheckout] = {}
        self._pw: Any = None
        self._lock = asyncio.Lock()

    def estado(self) -> list[dict]:
        return [s.como_dict() for s in self.sessoes.values()]

    def _pub(self, s: SessaoCheckout) -> None:
        barramento._publicar({"tipo": "checkout", "dados": s.como_dict()})
        barramento.log("INFO" if s.status != "erro" else "ERROR", f"checkout:{s.slug}", f"{s.status}: {s.mensagem}")

    async def iniciar(self, con: sqlite3.Connection, grupos: list[dict]) -> list[dict]:
        """grupos = [{slug, itens:[{sku, quantidade, seller, url, nome}]}] vindos do otimizador."""
        async with self._lock:
            from playwright.async_api import async_playwright
            if self._pw is None:
                self._pw = await async_playwright().start()
            tarefas = []
            for g in grupos:
                loja = con.execute("SELECT * FROM lojas WHERE slug=?", (g["slug"],)).fetchone()
                if not loja:
                    continue
                s = SessaoCheckout(g["slug"], loja["nome"], itens_total=len(g["itens"]))
                self.sessoes[g["slug"]] = s
                self._pub(s)
                itens = [ItemCarrinho(i["sku"], int(i["quantidade"]), i.get("seller"), i.get("url"), i.get("nome"))
                         for i in g["itens"]]
                tarefas.append(self._uma_loja(con, loja, s, itens))
            # todas as lojas ao mesmo tempo, cada uma na sua aba/contexto
            await asyncio.gather(*tarefas, return_exceptions=True)
        return self.estado()

    async def _uma_loja(self, con: sqlite3.Connection, loja: sqlite3.Row, s: SessaoCheckout,
                        itens: list[ItemCarrinho]) -> None:
        coletor = fabricar(loja, parametros(loja), None, None)  # type: ignore[arg-type]
        info = coletor.checkout()
        try:
            s.status, s.mensagem = "abrindo", "abrindo perfil persistente"
            self._pub(s)
            if s.contexto is not None:
                await s.contexto.close()
            perfil = config().perfis_dir / loja["slug"]
            perfil.mkdir(parents=True, exist_ok=True)
            ctx = await self._pw.chromium.launch_persistent_context(
                str(perfil), headless=config().checkout_headless, locale="pt-BR",
                viewport={"width": 1280, "height": 860},
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
            )
            s.contexto = ctx
            await ctx.route("**/*", _guarda_pagamento(s))
            cred = None
            try:
                cred = cofre.ler(con, loja["id_loja"])
            except cofre.CofreIndisponivel as e:
                s.alertas.append(str(e))
            if cred and cred.get("cookies"):
                await ctx.add_cookies(cred["cookies"])
            pg = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await pg.goto(coletor.base, wait_until="domcontentloaded", timeout=60000)

            if cred and cred.get("usuario") and cred.get("senha") and info.url_login:
                if not (info.seletor_logado and await pg.locator(info.seletor_logado).count()):
                    s.status, s.mensagem = "login", "entrando com credenciais do cofre"
                    self._pub(s)
                    await _login(pg, info, cred["usuario"], cred["senha"])

            s.status, s.mensagem = "montando", "adicionando itens"
            self._pub(s)
            link = coletor.link_carrinho(itens)
            if link:
                await pg.goto(link, wait_until="domcontentloaded", timeout=60000)
                s.itens_ok = len(itens)
            else:
                for it in itens:
                    if await _adicionar_pela_pagina(pg, it):
                        s.itens_ok += 1
                    else:
                        s.alertas.append(f"não consegui adicionar: {it.nome or it.sku}")
                    await asyncio.sleep(1.2)

            destino = info.url_revisao or coletor.base
            await pg.goto(destino, wait_until="domcontentloaded", timeout=60000)
            # salva os cookies (sessão renovada) de volta no cofre
            try:
                cofre.salvar(con, loja["id_loja"], cookies=await ctx.cookies())
            except cofre.CofreIndisponivel:
                pass
            await ctx.unroute("**/*")   # entrega para o humano: a partir daqui só você clica
            s.status, s.url_final = "pronto", pg.url
            s.mensagem = (f"{s.itens_ok}/{s.itens_total} itens no carrinho; revise e pague você mesmo. "
                          "O robô não confirma pagamento.")
            self._pub(s)
        except Exception as e:  # noqa: BLE001
            s.status, s.mensagem = "erro", f"{type(e).__name__}: {e}"
            self._pub(s)

    async def fechar(self, slug: str | None = None) -> None:
        for k, s in list(self.sessoes.items()):
            if slug and k != slug:
                continue
            if s.contexto is not None:
                try:
                    await s.contexto.close()
                except Exception:  # noqa: BLE001
                    pass
            self.sessoes.pop(k, None)


def _guarda_pagamento(s: SessaoCheckout):
    async def rota(route: Any) -> None:
        req = route.request
        if req.method in ("POST", "PUT", "PATCH") and ROTAS_PAGAMENTO.search(req.url):
            s.alertas.append(f"bloqueado pelo robô (pagamento): {req.method} {req.url[:120]}")
            await route.abort()
            return
        await route.continue_()
    return rota


async def _clique_seguro(loc: Any) -> bool:
    try:
        txt = (await loc.inner_text(timeout=2000)) or ""
    except Exception:  # noqa: BLE001
        txt = ""
    if PROIBIDO_CLICAR.search(txt):
        return False
    await loc.click(timeout=8000)
    return True


async def _login(pg: Any, info: Any, usuario: str, senha: str) -> None:
    await pg.goto(info.url_login, wait_until="domcontentloaded", timeout=60000)
    if info.extras.get("login_vtex_id"):
        # VTEX ID: escolhe "entrar com e-mail e senha" quando existir
        for t in ("Entrar com e-mail e senha", "E-mail e senha", "Entrar com email e senha"):
            b = pg.get_by_text(t, exact=False)
            if await b.count():
                await _clique_seguro(b.first)
                break
    await pg.locator(info.seletor_usuario).first.fill(usuario, timeout=15000)
    await pg.locator(info.seletor_senha).first.fill(senha, timeout=15000)
    await _clique_seguro(pg.locator(info.seletor_entrar).first)
    await pg.wait_for_timeout(4000)


async def _adicionar_pela_pagina(pg: Any, it: ItemCarrinho) -> bool:
    if not it.url:
        return False
    await pg.goto(it.url, wait_until="domcontentloaded", timeout=60000)
    await pg.wait_for_timeout(1500)
    for sel in SELETORES_ADICIONAR:
        loc = pg.locator(sel).first
        if await loc.count():
            if not await _clique_seguro(loc):
                continue
            for _ in range(max(0, it.quantidade - 1)):
                mais = pg.locator("button:has-text('+'), button[aria-label*='umentar'], [class*='increment']").first
                if await mais.count():
                    await mais.click()
                    await pg.wait_for_timeout(400)
            return True
    return False


gerente = GerenteCheckout()
