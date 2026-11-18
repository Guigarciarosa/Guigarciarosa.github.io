"""Irani Delivery (4 filiais): plataforma Mercafácil (Next.js) com GraphQL federado.

Sondagem (nov/2026):
  - GET https://fed-gateway.mercafacil.com/graphql, header `x-custom-origin: irani.delivery` escolhe o tenant
  - filial = argumento storeId (ecommerceId): 1 pacaembu (Av. Brasil), 2 parque-verde, 3 floresta,
    4 alto-alegre (Tancredo Neves). O mapa é relido de `stores{...}` a cada coleta.
  - departamentos: showcaseDepartments(storeId) (21 raízes; 99999/88888/77777 são vitrines virtuais)
  - ecommerceProducts(storeId, departmentId, page, pageSize) exige departamento ou busca, inclui os filhos
    e não devolve total: pagina (500 por página) até vir curta.
  - página do produto: /{filial}/produto/m/{slug}-{modelId}
  - Não expõe EAN; `id` muda por filial, `modelId` é o mesmo nas 4 -> usado como chave cruzada.
  - priceWithDiscount = 0 significa sem promoção; clubSale exige cadastro no clube (ignorado).
"""
from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator, Iterable
from urllib.parse import quote_plus

from ..matcher import ItemColetado
from .base import CheckoutInfo, Coletor, ItemCarrinho

GATEWAY = "https://fed-gateway.mercafacil.com/graphql"
CAMPOS = ("id modelId name slug image price priceWithDiscount discount stock brand department departmentId "
          "quantityDescription unitOfMeasurement { abbreviation } code")
FILIAIS_PADRAO = {"pacaembu": "1", "parque-verde": "2", "floresta": "3", "alto-alegre": "4"}
# departamentos vistos nas URLs do site (fallback se a página não listar)
DEPARTAMENTOS_PADRAO = ["1485", "1826", "1486", "1488", "1864", "1905", "1487", "1484", "1827", "1779", "1483",
                        "1480", "1479", "2050", "2068", "2073", "2082", "2301", "2227", "2083"]
VIRTUAIS = {"99999", "88888", "77777"}


class ColetorIrani(Coletor):
    plataforma = "irani"

    def __init__(self, *a: Any, **kw: Any):
        super().__init__(*a, **kw)
        self.filial = str(self.params.get("filial") or self.base.rstrip("/").split("/")[-1])
        self.store_id = str(self.params.get("store_id") or FILIAIS_PADRAO.get(self.filial, ""))
        self.page_size = int(self.params.get("page_size") or 500)
        self.origem = str(self.params.get("origem") or "irani.delivery")

    async def gql(self, query: str) -> dict:
        r = await self.cliente.get(GATEWAY, params={"query": query},
                                   headers={"x-custom-origin": self.origem, "apollo-require-preflight": "true",
                                            "Origin": "https://www.irani.delivery",
                                            "Referer": self.base + "/", "Accept": "application/json"})
        d = r.json()
        if d.get("errors") and not d.get("data"):
            raise RuntimeError(f"GraphQL: {str(d['errors'])[:200]}")
        return d.get("data") or {}

    async def _resolver_loja(self) -> None:
        try:
            d = await self.gql("{ stores { id ecommerceId name slug } }")
            for s in d.get("stores") or []:
                if s.get("slug") == self.filial and s.get("ecommerceId"):
                    self.store_id = str(s["ecommerceId"])
        except Exception as e:  # noqa: BLE001
            self.ctx.log("WARNING", f"stores{{}} falhou ({e}); usando storeId {self.store_id}")
        if not self.store_id:
            raise RuntimeError(f"filial '{self.filial}' sem storeId")

    async def departamentos(self) -> list[str]:
        try:
            d = await self.gql("{ showcaseDepartments(storeId:" + json.dumps(self.store_id) + ") { id name } }")
            ids = [str(x["id"]) for x in d.get("showcaseDepartments") or [] if str(x.get("id")) not in VIRTUAIS]
            if ids:
                return ids
        except Exception as e:  # noqa: BLE001
            self.ctx.log("WARNING", f"showcaseDepartments falhou ({e}); usando lista padrão")
        return list(self.params.get("departamentos") or DEPARTAMENTOS_PADRAO)

    def _consulta(self, departamento: str | None, pagina: int, busca: str | None = None) -> str:
        args = [f"storeId:{json.dumps(self.store_id)}", f"pageSize:{self.page_size}", f"page:{pagina}"]
        if departamento:
            args.append(f"departmentId:{json.dumps(departamento)}")
        if busca:
            args.append(f"search:{json.dumps(busca)}")
        return "{ ecommerceProducts(" + ", ".join(args) + ") { products { " + CAMPOS + " } } }"

    async def _pagina(self, departamento: str | None, pagina: int, busca: str | None = None) -> list[dict]:
        d = await self.gql(self._consulta(departamento, pagina, busca))
        return ((d.get("ecommerceProducts") or {}).get("products")) or []

    async def coletar(self) -> AsyncIterator[ItemColetado]:
        await self._resolver_loja()
        deps = await self.departamentos()
        self.ctx.log("INFO", f"filial {self.filial} (storeId {self.store_id}), {len(deps)} departamentos")
        vistos: set[str] = set()
        lidos = 0
        for i, dep in enumerate(deps):
            pagina = 1
            while pagina <= 400:
                try:
                    produtos = await self._pagina(dep, pagina)
                except Exception as e:  # noqa: BLE001
                    self.ctx.log("ERROR", f"departamento {dep} p{pagina}: {e}")
                    break
                for p in produtos:
                    it = self.converter(p)
                    if it and it.sku not in vistos:
                        vistos.add(it.sku)
                        lidos += 1
                        yield it
                # sem total na API: estima pelo ritmo (departamentos restantes x média)
                media = lidos / (i + 1) if i else lidos + self.page_size
                self.ctx.definir_total(int(lidos + media * (len(deps) - i - 1) + (self.page_size
                                       if len(produtos) == self.page_size else 0)), True)
                if len(produtos) < self.page_size:
                    break
                pagina += 1

    def converter(self, p: dict) -> ItemColetado | None:
        preco = float(p.get("price") or 0)
        promo = float(p.get("priceWithDiscount") or 0) or None
        if preco <= 0:
            return None
        nome = (p.get("name") or "").strip()
        qd = (p.get("quantityDescription") or "").strip()
        if qd and qd.lower() not in nome.lower():
            nome = f"{nome} {qd}"
        un = ((p.get("unitOfMeasurement") or {}).get("abbreviation") or "").lower()
        if un == "kg" and not re.search(r"\d\s*(kg|g)\b", nome.lower()):
            nome = f"{nome} 1kg"   # vendido a granel: preço por kg
        sku = str(p.get("modelId") or p.get("id"))
        return ItemColetado(
            sku=sku, nome=nome, preco=preco, preco_promocional=promo if promo and promo < preco else None,
            marca=p.get("brand"), disponivel=(p.get("stock") or 0) > 0, categoria=p.get("department"),
            imagem_url=p.get("image"), url=self.url_produto(p),
            chave_cruzada=f"mercafacil:{p['modelId']}" if p.get("modelId") else None,
            extra={"id_filial": p.get("id"), "slug": p.get("slug"), "code": p.get("code")},
        )

    def url_produto(self, p: dict) -> str:
        if p.get("slug") and p.get("modelId"):
            return f"{self.base}/produto/m/{p['slug']}-{p['modelId']}"
        return f"{self.base}/busca?q={quote_plus(p.get('name') or '')}"

    async def buscar(self, termo: str, limite: int = 20) -> list[ItemColetado]:
        await self._resolver_loja()
        return [x for p in await self._pagina(None, 1, termo) if (x := self.converter(p))][:limite]

    def link_carrinho(self, itens: Iterable[ItemCarrinho]) -> str | None:
        return None  # Mercafácil não tem deep link de carrinho: o worker adiciona pela página

    def checkout(self) -> CheckoutInfo:
        return CheckoutInfo(
            url_login=f"{self.base}/login",
            seletor_usuario="input[type='email'], input[name='email'], input[name='cpf'], input[type='tel']",
            seletor_senha="input[type='password']",
            seletor_entrar="button[type='submit']",
            url_revisao=f"{self.base}/carrinho",
            seletor_logado="[href*='minha-conta'], [href*='/perfil']",
        )
