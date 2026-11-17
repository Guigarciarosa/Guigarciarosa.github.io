"""Festval: VTEX IO (conta meufestval) com preço REGIONALIZADO.

Sondagem (nov/2026): a busca legada catalog_system ignora a região e mostra o seller 1 sem estoque.
A Intelligent Search respeita `regionId` (Cascavel != Curitiba: Baggio 250g R$ 39,98 x R$ 37,99).
O regionId sai de /api/checkout/pub/regions?country=BRA&postalCode=<CEP de Cascavel> e é resolvido
a cada coleta porque o id pode mudar.
"""
from __future__ import annotations

from typing import Any, AsyncIterator
from urllib.parse import urlparse

from ..matcher import ItemColetado
from .vtex import ColetorVTEX

IS_PAGINA = 50
IS_LIMITE = 2500


class ColetorFestval(ColetorVTEX):
    plataforma = "festval"

    def __init__(self, *a: Any, **kw: Any):
        super().__init__(*a, **kw)
        self.region_id: str | None = self.params.get("region_id")

    @property
    def cep(self) -> str:
        return str(self.params.get("cep") or "85801000")

    async def resolver_regiao(self) -> str | None:
        r = await self.cliente.get(f"{self.base}/api/checkout/pub/regions",
                                   params={"country": "BRA", "postalCode": self.cep})
        dados = r.json() or []
        if dados and dados[0].get("id"):
            self.region_id = dados[0]["id"]
            sellers = [s.get("id") for s in dados[0].get("sellers") or []]
            self.ctx.log("INFO", f"região CEP {self.cep}: {self.region_id[:12]}… sellers {', '.join(sellers)}")
        else:
            self.ctx.log("WARNING", f"CEP {self.cep} sem região; preços podem vir sem estoque")
        return self.region_id

    async def _is(self, facetas: str, pagina: int, query: str = "") -> tuple[list[dict], int]:
        params: dict[str, Any] = {"count": IS_PAGINA, "page": pagina, "locale": "pt-BR",
                                  "hideUnavailableItems": "false"}
        if query:
            params["query"] = query
        if self.region_id:
            params["regionId"] = self.region_id
        url = f"{self.base}/api/io/_v/api/intelligent-search/product_search/{facetas}".rstrip("/")
        r = await self.cliente.get(url, params=params, aceitar=(200, 206))
        d = r.json() or {}
        return d.get("products") or [], int(d.get("recordsFiltered") or 0)

    async def _arvore(self) -> list[dict]:
        r = await self.cliente.get(f"{self.base}/api/catalog_system/pub/category/tree/3")
        return r.json() or []

    @staticmethod
    def facetas(no: dict) -> str:
        partes = [p for p in urlparse(no.get("url") or "").path.split("/") if p]
        return "/".join(f"category-{i + 1}/{p}" for i, p in enumerate(partes))

    async def coletar(self) -> AsyncIterator[ItemColetado]:
        if not self.region_id:
            try:
                await self.resolver_regiao()
            except Exception as e:  # noqa: BLE001
                self.ctx.log("WARNING", f"não resolvi a região: {e}")
        raiz = await self._arvore()
        vistos: set[str] = set()
        fila: list[dict] = list(raiz)
        total_estimado = 0
        while fila:
            no = fila.pop(0)
            fac = self.facetas(no)
            if not fac:
                continue
            try:
                produtos, total = await self._is(fac, 1)
            except Exception as e:  # noqa: BLE001
                self.ctx.log("ERROR", f"{no.get('name')}: {e}")
                continue
            if total > IS_LIMITE and no.get("children"):
                fila = list(no["children"]) + fila   # quebra em subcategorias para passar do teto
                continue
            total_estimado += min(total, IS_LIMITE)
            self.ctx.definir_total(total_estimado, bool(fila))
            nome_cat = no.get("name")
            for it in self._itens(produtos, nome_cat, vistos):
                yield it
            paginas = -(-min(total, IS_LIMITE) // IS_PAGINA)
            for pg in range(2, paginas + 1):
                try:
                    produtos, _ = await self._is(fac, pg)
                except Exception as e:  # noqa: BLE001
                    self.ctx.log("ERROR", f"{nome_cat} p{pg}: {e}")
                    break
                if not produtos:
                    break
                for it in self._itens(produtos, nome_cat, vistos):
                    yield it

    def converter(self, p: dict, item: dict, nome_cat: str | None = None) -> ItemColetado | None:
        # com região, o seller regional (meufestval<loja>) é o que tem estoque: prefere ele
        sellers = item.get("sellers") or []
        disp = [s for s in sellers if (s.get("commertialOffer") or {}).get("AvailableQuantity")]
        if disp:
            item = dict(item, sellers=[dict(disp[0], sellerDefault=True)] + [s for s in sellers if s is not disp[0]])
        it = super().converter(p, item, nome_cat)
        if it and it.ean and len(str(it.ean)) < 8:
            it.ean = None  # PLU de balança (ex.: pão francês "1311") não é GTIN
        return it

    async def buscar(self, termo: str, limite: int = 20) -> list[ItemColetado]:
        if not self.region_id:
            await self.resolver_regiao()
        produtos, _ = await self._is("", 1, termo)
        return [x for p in produtos for item in p.get("items") or [] if (x := self.converter(p, item))][:limite]
