"""Coletor VTEX (Super Muffato, e Festval se for VTEX) usando as APIs públicas de catálogo.

- Árvore: /api/catalog_system/pub/category/tree/3
- Busca:  /api/catalog_system/pub/products/search?fq=C:/1/2/&_from=0&_to=49&sc=22
  O header `resources: 0-49/812` dá o total da categoria (barra de progresso real).
  A VTEX limita a busca a 2.500 resultados por consulta; categorias maiores são lidas
  nas duas ordens (nome A-Z e Z-A) para cobrir até 5.000.
- Carrinho: /checkout/cart/add?sku=..&qty=..&seller=..&sc=..
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Iterable
from urllib.parse import urlencode

from ..config import config
from ..matcher import ItemColetado
from .base import CheckoutInfo, Coletor, ItemCarrinho

PAGINA = 50
LIMITE_VTEX = 2500


class ColetorVTEX(Coletor):
    plataforma = "vtex"

    @property
    def sc(self) -> str:
        return str(self.params.get("sc") or "")

    def _p(self, extra: dict[str, Any]) -> dict[str, Any]:
        p = dict(extra)
        if self.sc:
            p["sc"] = self.sc
        return p

    async def categorias(self) -> list[tuple[str, str]]:
        r = await self.cliente.get(f"{self.base}/api/catalog_system/pub/category/tree/3", params=self._p({}))
        folhas: list[tuple[str, str]] = []

        def andar(nos: list[dict], caminho: list[str], nomes: list[str]) -> None:
            for n in nos:
                c = caminho + [str(n["id"])]
                nm = nomes + [n.get("name", "")]
                filhos = n.get("children") or []
                if filhos:
                    andar(filhos, c, nm)
                else:
                    folhas.append(("/" + "/".join(c) + "/", " > ".join(nm)))

        andar(r.json(), [], [])
        return folhas

    async def _pagina(self, fq: str, ini: int, ordem: str | None = None) -> tuple[list[dict], int]:
        params = {"fq": fq, "_from": ini, "_to": ini + PAGINA - 1}
        if ordem:
            params["O"] = ordem
        r = await self.cliente.get(f"{self.base}/api/catalog_system/pub/products/search",
                                   params=self._p(params), aceitar=(200, 206))
        total = 0
        rec = r.headers.get("resources", "")
        if "/" in rec:
            try:
                total = int(rec.split("/")[-1])
            except ValueError:
                total = 0
        return r.json() or [], total

    async def coletar(self) -> AsyncIterator[ItemColetado]:
        cats = await self.categorias()
        self.ctx.log("INFO", f"{len(cats)} categorias-folha")
        vistos: set[str] = set()
        total_conhecido = 0
        pendentes: list[tuple[str, str, int]] = []
        max_pag = config().max_paginas_categoria

        # 1ª passada: primeira página de cada categoria (descobre os totais)
        for i, (caminho, nome_cat) in enumerate(cats):
            try:
                produtos, total = await self._pagina(f"C:{caminho}", 0)
            except FileNotFoundError:
                continue
            except Exception as e:  # noqa: BLE001
                self.ctx.log("ERROR", f"categoria {nome_cat}: {e}")
                continue
            total_conhecido += min(total, LIMITE_VTEX * 2)
            restantes = len(cats) - i - 1
            media = total_conhecido / (i + 1)
            self.ctx.definir_total(int(total_conhecido + media * restantes), restantes > 0)
            if total > PAGINA:
                pendentes.append((caminho, nome_cat, total))
            for it in self._itens(produtos, nome_cat, vistos):
                yield it

        self.ctx.definir_total(total_conhecido, False)
        for caminho, nome_cat, total in pendentes:
            limite = min(total, LIMITE_VTEX)
            for ini in range(PAGINA, min(limite, PAGINA * max_pag), PAGINA):
                try:
                    produtos, _ = await self._pagina(f"C:{caminho}", ini)
                except Exception as e:  # noqa: BLE001
                    self.ctx.log("ERROR", f"{nome_cat} página {ini}: {e}")
                    break
                if not produtos:
                    break
                for it in self._itens(produtos, nome_cat, vistos):
                    yield it
            if total > LIMITE_VTEX:  # lê o "rabo" na ordem inversa
                self.ctx.log("WARNING", f"{nome_cat}: {total} itens (> {LIMITE_VTEX}); lendo também em ordem Z-A")
                for ini in range(0, min(total - LIMITE_VTEX, LIMITE_VTEX), PAGINA):
                    try:
                        produtos, _ = await self._pagina(f"C:{caminho}", ini, "OrderByNameDESC")
                    except Exception as e:  # noqa: BLE001
                        self.ctx.log("ERROR", f"{nome_cat} Z-A {ini}: {e}")
                        break
                    for it in self._itens(produtos, nome_cat, vistos):
                        yield it

    def _itens(self, produtos: list[dict], nome_cat: str, vistos: set[str]) -> Iterable[ItemColetado]:
        for p in produtos:
            for item in p.get("items") or []:
                sku = str(item.get("itemId") or "")
                if not sku or sku in vistos:
                    continue
                vistos.add(sku)
                it = self.converter(p, item, nome_cat)
                if it:
                    yield it

    def converter(self, p: dict, item: dict, nome_cat: str | None = None) -> ItemColetado | None:
        sellers = item.get("sellers") or []
        seller = next((s for s in sellers if s.get("sellerDefault")), sellers[0] if sellers else None)
        if not seller:
            return None
        of = seller.get("commertialOffer") or {}
        preco = float(of.get("Price") or 0)
        lista = float(of.get("ListPrice") or of.get("PriceWithoutDiscount") or 0)
        disponivel = bool(of.get("IsAvailable", (of.get("AvailableQuantity") or 0) > 0))
        if preco <= 0 and lista <= 0:
            return None
        base_preco, promo = (lista, preco) if lista > preco > 0 else (preco or lista, None)
        imgs = item.get("images") or []
        nome = item.get("nameComplete") or p.get("productName") or item.get("name") or ""
        cats = p.get("categories") or []
        return ItemColetado(
            sku=str(item["itemId"]), nome=nome, preco=base_preco, preco_promocional=promo,
            marca=p.get("brand"), ean=item.get("ean") or None, disponivel=disponivel,
            categoria=(cats[0].strip("/").split("/")[-1] if cats else nome_cat),
            imagem_url=imgs[0].get("imageUrl") if imgs else None,
            url=p.get("link") or f"{self.base}/{p.get('linkText', '')}/p",
            seller=str(seller.get("sellerId") or "1"),
        )

    async def buscar(self, termo: str, limite: int = 20) -> list[ItemColetado]:
        r = await self.cliente.get(f"{self.base}/api/catalog_system/pub/products/search",
                                   params=self._p({"ft": termo, "_from": 0, "_to": limite - 1}), aceitar=(200, 206))
        out: list[ItemColetado] = []
        for p in r.json() or []:
            for item in p.get("items") or []:
                it = self.converter(p, item)
                if it:
                    out.append(it)
        return out

    def link_carrinho(self, itens: Iterable[ItemCarrinho]) -> str | None:
        pares: list[tuple[str, str]] = []
        for i in itens:
            pares += [("sku", i.sku), ("qty", str(i.quantidade)), ("seller", i.seller or "1")]
        if not pares:
            return None
        pares.append(("redirect", "true"))
        if self.sc:
            pares.append(("sc", self.sc))
        return f"{self.base}/checkout/cart/add?{urlencode(pares)}"

    def checkout(self) -> CheckoutInfo:
        return CheckoutInfo(
            url_login=f"{self.base}/login",
            seletor_usuario="input[name='email'], input[type='email']",
            seletor_senha="input[type='password']",
            seletor_entrar="button[type='submit'], button:has-text('Entrar')",
            url_revisao=f"{self.base}/checkout/#/cart",
            seletor_logado=".vtex-login-2-x-profile, .welcome-message, [class*='userName']",
            extras={"login_vtex_id": True},
        )
