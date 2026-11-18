"""Contrato comum dos coletores."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Iterable

from ..http import ClienteEducado
from ..matcher import ItemColetado


@dataclass
class Contexto:
    """O orquestrador passa isto para o coletor reportar progresso e logs."""
    definir_total: Callable[[int, bool], None] = lambda total, estimado=True: None
    log: Callable[[str, str], None] = lambda nivel, msg: None
    bloqueio: Callable[[str], None] = lambda msg: None


@dataclass
class ItemCarrinho:
    sku: str
    quantidade: int
    seller: str | None = None
    url: str | None = None
    nome: str | None = None


@dataclass
class CheckoutInfo:
    """Como automatizar o checkout desta plataforma (usado pelo worker Playwright)."""
    url_login: str | None = None
    seletor_usuario: str | None = None
    seletor_senha: str | None = None
    seletor_entrar: str | None = None
    url_revisao: str | None = None           # para onde ir depois de montar o carrinho
    seletor_logado: str | None = None        # presença indica sessão válida
    extras: dict[str, Any] = field(default_factory=dict)


class Coletor:
    plataforma = "base"

    def __init__(self, loja: sqlite3.Row | dict, params: dict[str, Any], cliente: ClienteEducado,
                 ctx: Contexto | None = None):
        self.loja = loja
        self.params = params
        self.cliente = cliente
        self.ctx = ctx or Contexto()
        self.base = str(loja["url_base"]).rstrip("/")

    async def coletar(self) -> AsyncIterator[ItemColetado]:  # pragma: no cover - interface
        raise NotImplementedError
        yield  # noqa

    def link_carrinho(self, itens: Iterable[ItemCarrinho]) -> str | None:
        """Deep link que monta o carrinho no navegador do usuário, quando a loja suporta."""
        return None

    def checkout(self) -> CheckoutInfo:
        return CheckoutInfo()


# ---------------------------------------------------------------- utilidades
def preco_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d,.\-]", "", str(v))
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def primeiro(d: dict, *chaves: str, padrao: Any = None) -> Any:
    for c in chaves:
        cur: Any = d
        for parte in c.split("."):
            if isinstance(cur, dict) and parte in cur:
                cur = cur[parte]
            elif isinstance(cur, list) and parte.isdigit() and int(parte) < len(cur):
                cur = cur[int(parte)]
            else:
                cur = None
                break
        if cur not in (None, "", [], {}):
            return cur
    return padrao


_CHAVES_NOME = ("name", "nome", "productName", "title", "titulo", "descricao", "description", "item_name")
_CHAVES_PRECO = ("price", "preco", "valor", "sale_price", "salePrice", "Price", "bestPrice", "prices.0.price",
                 "preco_venda", "valor_venda", "selling_price", "price.value")


def parece_produto(d: Any) -> bool:
    if not isinstance(d, dict):
        return False
    tem_nome = any(isinstance(d.get(k), str) and len(d.get(k)) > 2 for k in _CHAVES_NOME)
    tem_preco = any(primeiro(d, k) is not None for k in _CHAVES_PRECO)
    return tem_nome and tem_preco


def extrair_produtos_json(obj: Any, limite: int = 20000) -> list[dict]:
    """Varre JSON arbitrário e devolve dicionários que parecem produto (fallback para SPAs)."""
    achados: list[dict] = []
    pilha = [obj]
    while pilha and len(achados) < limite:
        cur = pilha.pop()
        if isinstance(cur, list):
            if cur and sum(parece_produto(x) for x in cur[:5]) >= min(2, len(cur[:5])):
                achados.extend(x for x in cur if parece_produto(x))
                continue
            pilha.extend(cur)
        elif isinstance(cur, dict):
            pilha.extend(v for v in cur.values() if isinstance(v, (list, dict)))
    return achados


def item_generico(d: dict, base_url: str = "") -> ItemColetado | None:
    nome = primeiro(d, *_CHAVES_NOME)
    preco = preco_float(primeiro(d, *_CHAVES_PRECO))
    if not nome or preco is None or preco <= 0:
        return None
    promo = preco_float(primeiro(d, "promo_price", "preco_promocional", "promotional_price", "prices.0.promo_price",
                                 "price_promo", "precoPromocional", "special_price"))
    de = preco_float(primeiro(d, "list_price", "listPrice", "ListPrice", "preco_de", "old_price", "price_from"))
    if de and de > preco:
        promo, preco = preco, de
    sku = primeiro(d, "sku", "id", "codigo", "code", "itemId", "product_id", "_id", "slug")
    if sku is None:
        return None
    ean = primeiro(d, "ean", "gtin", "barcode", "barcodes.0", "codigo_barras", "ean13", "EAN")
    img = primeiro(d, "image", "imagem", "image_url", "imageUrl", "images.0", "images.0.url", "thumbnail",
                   "foto", "picture")
    if isinstance(img, dict):
        img = primeiro(img, "url", "src", "large", "imageUrl")
    url = primeiro(d, "url", "link", "permalink", "slug")
    if isinstance(url, str) and url and not url.startswith("http"):
        url = base_url.rstrip("/") + "/" + url.lstrip("/")
    disponivel = primeiro(d, "available", "disponivel", "in_stock", "isAvailable", padrao=True)
    estoque = primeiro(d, "stock", "estoque", "available_stock", "quantity")
    if isinstance(estoque, (int, float)) and estoque <= 0:
        disponivel = False
    marca = primeiro(d, "brand", "marca", "fabricante")
    if isinstance(marca, dict):
        marca = primeiro(marca, "name", "nome", "title")
    categoria = primeiro(d, "category", "categoria", "category_name")
    if isinstance(categoria, dict):
        categoria = primeiro(categoria, "name", "nome", "title")
    elif isinstance(categoria, list):
        categoria = next((c if isinstance(c, str) else primeiro(c, "name", "nome") for c in categoria), None)
    return ItemColetado(
        sku=str(sku), nome=str(nome).strip(), preco=preco, preco_promocional=promo,
        marca=marca if isinstance(marca, str) else None,
        ean=str(ean) if ean else None, disponivel=bool(disponivel),
        categoria=categoria if isinstance(categoria, str) else None,
        imagem_url=img if isinstance(img, str) else None, url=url if isinstance(url, str) else None,
    )
