"""Muffatão: loja Instabuy (muffataosupermercado.instabuy.app.br).

Sondagem (nov/2026): a vitrine responde "Sorry, you have been blocked" (Cloudflare) para clientes
automatizados e os caminhos apiv3 conhecidos devolvem 404. Decisão de projeto: o sistema NÃO tenta
contornar um bloqueio explícito (nada de navegador headless disfarçado). A loja vem desativada e só
coleta quando `api_itens` (endpoint JSON real, visto no DevTools do seu navegador) estiver nos
parâmetros da loja: modelo de URL com {pagina}, {categoria} e {subdominio}, mais `categorias`.
Se a API também responder 403/challenge, a coleta para e registra o bloqueio.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Iterable

from ..matcher import ItemColetado
from .base import CheckoutInfo, Coletor, ItemCarrinho, extrair_produtos_json, item_generico


class ColetorInstabuy(Coletor):
    plataforma = "instabuy"

    async def coletar(self) -> AsyncIterator[ItemColetado]:
        vistos: set[str] = set()
        modelo = self.params.get("api_itens")
        if modelo:
            async for it in self._pela_api(modelo, vistos):
                yield it
            if vistos:
                return
        else:
            raise RuntimeError("Muffatão bloqueia coleta automática (Cloudflare). Informe 'api_itens' nos "
                               "parâmetros da loja com o endpoint JSON visto no DevTools, ou deixe a loja desativada.")

    async def _pela_api(self, modelo: str, vistos: set[str]) -> AsyncIterator[ItemColetado]:
        categorias = self.params.get("categorias") or [""]
        for cat in categorias:
            for pagina in range(1, 500):
                url = modelo.format(pagina=pagina, categoria=cat, subdominio=self.params.get("subdominio", ""))
                try:
                    r = await self.cliente.get(url, headers={"Referer": self.base + "/"})
                except Exception as e:  # noqa: BLE001
                    self.ctx.log("ERROR", f"api_itens {url}: {e}")
                    break
                novos = 0
                for d in extrair_produtos_json(r.json()):
                    it = self._converter(d)
                    if it and it.sku not in vistos:
                        vistos.add(it.sku)
                        novos += 1
                        yield it
                self.ctx.definir_total(len(vistos) + (50 if novos else 0), bool(novos))
                if not novos:
                    break

    def _converter(self, d: dict) -> ItemColetado | None:
        it = item_generico(d, self.base)
        if not it:
            return None
        # formatos Instabuy conhecidos: prices[{price, promo_price}], barcodes[], images["<arquivo>"]
        precos = d.get("prices") or []
        if precos and isinstance(precos, list) and isinstance(precos[0], dict):
            p0 = precos[0]
            preco = float(p0.get("price") or it.preco)
            promo = p0.get("promo_price")
            it.preco, it.preco_promocional = preco, (float(promo) if promo and float(promo) < preco else None)
        if isinstance(d.get("barcodes"), list) and d["barcodes"]:
            it.ean = str(d["barcodes"][0])
        img = it.imagem_url
        if img and not img.startswith(("http", "//")):
            it.imagem_url = f"https://ibassets.com.br/ib.item.image.large/l-{img}"
        if d.get("slug") and (not it.url or it.url.endswith(str(d.get("slug")))):
            it.url = f"{self.base}/p/{d['slug']}"
        return it

    def link_carrinho(self, itens: Iterable[ItemCarrinho]) -> str | None:
        return None

    def checkout(self) -> CheckoutInfo:
        return CheckoutInfo(
            url_login=f"{self.base}/login",
            seletor_usuario="input[type='email'], input[name='email'], input[name='login']",
            seletor_senha="input[type='password']",
            seletor_entrar="button[type='submit']",
            url_revisao=f"{self.base}/carrinho",
            seletor_logado="[href*='minha-conta'], [href*='perfil']",
        )
