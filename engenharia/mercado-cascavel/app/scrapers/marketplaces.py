"""Watchlist de marketplaces: só busca os termos cadastrados (nunca o catálogo inteiro).

Mercado Livre: API pública de busca; se pedir token (403), cai para o HTML da listagem.
Amazon: HTML de /s?k= (cards data-component-type="s-search-result").
Shopee: API v4 de busca. Sondagem (nov/2026): 403 anti-bot (erro 90309999). Sem contornar: vem
        desativada; o caminho oficial é a Open API de afiliados da Shopee.
Política: bloqueio explícito (403/challenge) encerra a busca daquele marketplace; não há disfarce.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import quote_plus, urljoin

from rapidfuzz import fuzz
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..http import ClienteEducado
from .base import preco_float


@dataclass
class Oferta:
    titulo: str
    preco: float
    url: str
    imagem_url: str | None = None
    score: float = 0.0


def pontuar(termo: str, ofertas: list[Oferta], minimo: float = 60.0, limite: int = 8) -> list[Oferta]:
    for o in ofertas:
        o.score = float(fuzz.token_set_ratio(termo.lower(), o.titulo.lower()))
    boas = [o for o in ofertas if o.score >= minimo and o.preco > 0]
    boas.sort(key=lambda o: (-round(o.score / 10), o.preco))
    return boas[:limite]


async def mercado_livre(cliente: ClienteEducado, termo: str) -> list[Oferta]:
    """Sondagem (nov/2026): a API de busca responde 403 sem token e o HTML cai em verificação de conta.
    Com ML_ACCESS_TOKEN (app grátis em developers.mercadolivre.com.br) a API oficial volta a responder."""
    token = os.getenv("ML_ACCESS_TOKEN", "")
    try:
        r = await cliente.get("https://api.mercadolibre.com/sites/MLB/search",
                              params={"q": termo, "limit": 20}, aceitar=(200,),
                              headers={"Authorization": f"Bearer {token}"} if token else None)
        dados = r.json()
        return [Oferta(x["title"], float(x["price"]), x["permalink"],
                       (x.get("thumbnail") or "").replace("-I.jpg", "-O.jpg") or None)
                for x in dados.get("results", []) if x.get("price")]
    except Exception:  # noqa: BLE001 - API fechada sem token: tenta o HTML
        pass
    slug = re.sub(r"\s+", "-", termo.strip().lower())
    r = await cliente.get(f"https://lista.mercadolivre.com.br/{quote_plus(slug, safe='-')}",
                          headers={"Accept": "text/html"})
    return parse_ml_html(r.texto)


def parse_ml_html(html: str) -> list[Oferta]:
    doc = HTMLParser(html)
    out: list[Oferta] = []
    for card in doc.css("li.ui-search-layout__item, div.poly-card, div.ui-search-result__wrapper"):
        a = card.css_first("a.poly-component__title, h3 a, a.ui-search-link, a.ui-search-item__group__element")
        frac = card.css_first(".poly-price__current .andes-money-amount__fraction, .andes-money-amount__fraction")
        if not a or not frac:
            continue
        cents = card.css_first(".poly-price__current .andes-money-amount__cents, .andes-money-amount__cents")
        preco = preco_float(frac.text().replace(".", "") + ("," + cents.text() if cents else ""))
        img = card.css_first("img")
        src = img.attributes.get("data-src") or img.attributes.get("src") if img else None
        if preco:
            out.append(Oferta(a.text(strip=True), preco, a.attributes.get("href", "").split("#")[0], src))
    return out


async def amazon(cliente: ClienteEducado, termo: str) -> list[Oferta]:
    r = await cliente.get("https://www.amazon.com.br/s", params={"k": termo},
                          headers={"Accept": "text/html", "Accept-Language": "pt-BR,pt;q=0.9"})
    return parse_amazon_html(r.texto)


def parse_amazon_html(html: str) -> list[Oferta]:
    doc = HTMLParser(html)
    out: list[Oferta] = []
    for card in doc.css('div[data-component-type="s-search-result"]'):
        asin = card.attributes.get("data-asin")
        titulo = card.css_first("h2 span") or card.css_first("h2")
        preco_el = card.css_first(".a-price:not(.a-text-price) .a-offscreen") or card.css_first(".a-price .a-offscreen")
        if not asin or not titulo or not preco_el:
            continue
        preco = preco_float(preco_el.text())
        img = card.css_first("img.s-image")
        if preco:
            out.append(Oferta(titulo.text(strip=True), preco, f"https://www.amazon.com.br/dp/{asin}",
                              img.attributes.get("src") if img else None))
    return out


async def shopee(cliente: ClienteEducado, termo: str) -> list[Oferta]:
    url = ("https://shopee.com.br/api/v4/search/search_items?by=relevancy&keyword=" + quote_plus(termo) +
           "&limit=30&newest=0&order=desc&page_type=search&scenario=PAGE_GLOBAL_SEARCH&version=2")
    r = await cliente.get(url, headers={"Referer": "https://shopee.com.br/"})
    return parse_shopee_json(r.json())


def parse_shopee_json(dados: dict) -> list[Oferta]:
    out: list[Oferta] = []
    for x in (dados or {}).get("items") or []:
        b = x.get("item_basic") or x
        if not b.get("name"):
            continue
        preco = (b.get("price_min") or b.get("price") or 0) / 100000
        url = f"https://shopee.com.br/product/{b.get('shopid')}/{b.get('itemid')}"
        img = f"https://down-br.img.susercontent.com/file/{b['image']}" if b.get("image") else None
        if preco > 0:
            out.append(Oferta(b["name"], round(preco, 2), url, img))
    return out


BUSCADORES = {"mercadolivre": mercado_livre, "amazon": amazon, "shopee": shopee}
