import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app import coleta
from app.database import loja_por_slug, parametros
from app.scrapers import fabricar
from app.scrapers.base import Contexto, ItemCarrinho, extrair_produtos_json, item_generico
from app.scrapers.marketplaces import parse_amazon_html, parse_ml_html, parse_shopee_json, pontuar

from .conftest import PNG, cliente_mock, vtex_produto

ARVORE = [{"id": 1, "name": "Mercearia", "url": "https://loja/mercearia", "hasChildren": True,
           "children": [{"id": 2, "name": "Café", "url": "https://loja/mercearia/cafe", "children": []}]}]

MUFFATO = [
    vtex_produto(10, "100", "Café Torrado e Moído Baggio Gourmet 500g", "Baggio", "7898952170000", 19.9, 24.9),
    vtex_produto(11, "101", "Café Solúvel Melitta Lata 150g", "Melitta", "7896348301489", 19.46, 27.8),
    vtex_produto(12, "102", "Detergente Ypê Neutro 500ml", "Ypê", None, 2.59, disp=False),
]


def rotas_muffato(req: httpx.Request) -> httpx.Response:
    u = urlparse(str(req.url))
    q = parse_qs(u.query)
    if u.path.endswith("/category/tree/3"):
        return httpx.Response(200, json=ARVORE)
    if u.path.endswith("/products/search"):
        assert q.get("sc") == ["22"]
        ini = int(q["_from"][0])
        dados = MUFFATO[ini:ini + 50] if q["fq"][0] == "C:/1/2/" else []
        return httpx.Response(206, json=dados, headers={"resources": f"{ini}-{ini + 49}/{len(MUFFATO)}"})
    if "img.x" in u.netloc:
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
    return httpx.Response(404)


@pytest.mark.asyncio
async def test_vtex_coleta_e_link_carrinho(con):
    loja = loja_por_slug(con, "muffato")
    async with cliente_mock(rotas_muffato) as cli:
        col = fabricar(loja, parametros(loja), cli, Contexto())
        itens = [i async for i in col.coletar()]
    assert len(itens) == 3
    baggio = itens[0]
    assert baggio.preco == 24.9 and baggio.preco_promocional == 19.9 and baggio.ean == "7898952170000"
    assert itens[2].disponivel is False
    link = col.link_carrinho([ItemCarrinho("100", 2, "1"), ItemCarrinho("101", 1, "1")])
    assert link.startswith("https://www.supermuffato.com.br/checkout/cart/add?sku=100&qty=2&seller=1&sku=101")
    assert "sc=22" in link


IRANI_PAG = [
    {"id": "587024", "modelId": "194314", "name": "Biscoito Isabela Tortinhas 140g Chocolate Avelã",
     "slug": "biscoito", "image": "https://img.x/b.png", "price": 3.29, "priceWithDiscount": 2.99,
     "stock": 200, "brand": "Isabela", "department": "Mercearia", "unitOfMeasurement": {"abbreviation": "un"}},
    {"id": "587025", "modelId": "1", "name": "CAFE BAGGIO GOURMET 500G", "price": 21.9, "priceWithDiscount": 0,
     "stock": 5, "brand": "Baggio", "department": "Mercearia", "unitOfMeasurement": {"abbreviation": "un"}},
    {"id": "587026", "modelId": "2", "name": "Banana Prata", "price": 6.99, "priceWithDiscount": 0,
     "stock": 50, "brand": None, "department": "Hortifruti", "unitOfMeasurement": {"abbreviation": "kg"}},
]


def rotas_irani(req: httpx.Request) -> httpx.Response:
    u = urlparse(str(req.url))
    if u.netloc == "fed-gateway.mercafacil.com":
        assert req.headers["x-custom-origin"] == "irani.delivery"
        q = parse_qs(u.query)["query"][0]
        if "showcaseDepartments" in q:
            return httpx.Response(200, json={"data": {"showcaseDepartments": [{"id": "1905", "name": "Mercearia"},
                                                                               {"id": "99999", "name": "Promoções"}]}})
        if "stores" in q:
            return httpx.Response(200, json={"data": {"stores": [{"id": "a", "ecommerceId": "2", "slug": "parque-verde"}]}})
        assert 'storeId:"2"' in q
        prods = IRANI_PAG if "page:1" in q and 'departmentId:"1905"' in q else []
        return httpx.Response(200, json={"data": {"ecommerceProducts": {"products": prods}}})
    if u.path == "/parque-verde":
        return httpx.Response(200, text='<a href="/parque-verde/mercearia-e-alimentos-1905">x</a>'
                                         '<a href="/parque-verde/promocoes-99999">p</a>')
    if "img.x" in u.netloc:
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
    return httpx.Response(404)


@pytest.mark.asyncio
async def test_irani_graphql(con):
    loja = loja_por_slug(con, "irani-parque-verde")
    async with cliente_mock(rotas_irani) as cli:
        col = fabricar(loja, parametros(loja), cli, Contexto())
        itens = [i async for i in col.coletar()]
    assert [i.sku for i in itens] == ["194314", "1", "2"]
    assert itens[0].preco_promocional == 2.99 and itens[0].chave_cruzada == "mercafacil:194314"
    assert itens[1].preco_promocional is None
    assert itens[2].nome.endswith("1kg")
    assert itens[0].url == "https://www.irani.delivery/parque-verde/produto/m/biscoito-194314"


def rotas_festval(req: httpx.Request) -> httpx.Response:
    u = urlparse(str(req.url))
    q = parse_qs(u.query)
    if u.path.endswith("/checkout/pub/regions"):
        assert q["postalCode"] == ["85801000"]
        return httpx.Response(200, json=[{"id": "v2.CASCAVEL", "sellers": [{"id": "meufestvalcarlosgomes"}]}])
    if u.path.endswith("/category/tree/3"):
        return httpx.Response(200, json=ARVORE)
    if "/intelligent-search/product_search/" in u.path:
        assert q.get("regionId") == ["v2.CASCAVEL"]
        assert u.path.endswith("category-1/mercearia")
        p = vtex_produto(20, "200", "Café Baggio Chocolate c/ Avelã 250g", "Baggio", "7898952174565", 39.98,
                         seller="meufestvalcarlosgomes")
        p["items"][0]["sellers"].insert(0, {"sellerId": "1", "commertialOffer": {"Price": 37.99, "AvailableQuantity": 0}})
        pao = vtex_produto(21, "201", "Pão Francês Festval 80g", "Festval", "1311", 14.99)
        return httpx.Response(200, json={"products": [p, pao] if q["page"] == ["1"] else [], "recordsFiltered": 2})
    return httpx.Response(404)


@pytest.mark.asyncio
async def test_festval_regiao(con):
    loja = loja_por_slug(con, "festval")
    async with cliente_mock(rotas_festval) as cli:
        col = fabricar(loja, parametros(loja), cli, Contexto())
        itens = [i async for i in col.coletar()]
    assert itens[0].preco == 39.98 and itens[0].seller == "meufestvalcarlosgomes"
    assert itens[1].ean is None  # PLU descartado


@pytest.mark.asyncio
async def test_coleta_unifica_entre_lojas_e_baixa_imagem_uma_vez(con, cfg):
    async with cliente_mock(rotas_muffato) as cli:
        r1 = await coleta.coletar_loja(con, "muffato", cli)
    async with cliente_mock(rotas_irani) as cli:
        r2 = await coleta.coletar_loja(con, "irani-parque-verde", cli)
    assert r1["status"] == "ok" and r1["novos"] == 3 and r1["lidos"] == 3
    assert r2["unificados"] == 1  # Café Baggio Gourmet 500g
    assert con.execute("SELECT COUNT(*) FROM produtos_mestres").fetchone()[0] == 5
    baggio = con.execute("SELECT id_produto, imagem_local_path FROM produtos_mestres WHERE nome_exibicao LIKE '%Baggio%'").fetchone()
    n_lojas = con.execute("SELECT COUNT(*) FROM precos_atuais WHERE id_produto=?", (baggio[0],)).fetchone()[0]
    assert n_lojas == 2
    arquivos = list(cfg.media_dir.rglob("*.*"))
    assert len(arquivos) == 4  # 3 do Muffato + biscoito do Irani; o café unificado não baixa de novo
    # segunda coleta: nada novo, nada alterado
    async with cliente_mock(rotas_muffato) as cli:
        r3 = await coleta.coletar_loja(con, "muffato", cli)
    assert r3["novos"] == 0 and r3["precos_atualizados"] == 0


def test_extracao_generica_de_spa():
    corpo = {"data": {"items": [
        {"id": 9, "name": "Arroz Camil 5kg", "prices": [{"price": 29.9, "promo_price": 25.9}], "barcodes": ["7896006716112"]},
        {"id": 10, "name": "Feijão Camil 1kg", "price": "8,49", "brand": {"name": "Camil"}},
    ]}}
    achados = extrair_produtos_json(corpo)
    assert len(achados) == 2
    it = item_generico(achados[1])
    assert it.preco == 8.49 and it.marca == "Camil"


def test_parsers_marketplace():
    amz = '''<div data-component-type="s-search-result" data-asin="B0X1"><h2><span>Café Baggio Gourmet 500g</span></h2>
      <span class="a-price"><span class="a-offscreen">R$ 27,50</span></span><img class="s-image" src="https://m.media/1.jpg"></div>'''
    o = parse_amazon_html(amz)
    assert o[0].preco == 27.5 and o[0].url.endswith("/dp/B0X1")
    ml = '''<li class="ui-search-layout__item"><div class="poly-card"><a class="poly-component__title" href="https://ml/x#p">Café Baggio 500g</a>
      <div class="poly-price__current"><span class="andes-money-amount__fraction">1.029</span><span class="andes-money-amount__cents">90</span></div></div></li>'''
    o = parse_ml_html(ml)
    assert o[0].preco == 1029.9 and o[0].url == "https://ml/x"
    sh = {"items": [{"item_basic": {"name": "Detergente Ypê 5L", "price_min": 2590000, "shopid": 1, "itemid": 2, "image": "abc"}}]}
    o = parse_shopee_json(sh)
    assert o[0].preco == 25.9
    r = pontuar("detergente ype 5l", o + parse_amazon_html(amz))
    assert r and r[0].titulo.startswith("Detergente")
