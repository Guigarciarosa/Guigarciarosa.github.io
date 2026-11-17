import pytest
from fastapi.testclient import TestClient

from app import otimizador
from app.checkout.worker import PROIBIDO_CLICAR, ROTAS_PAGAMENTO
from app.database import loja_por_slug, registrar_preco, transacao
from app.matcher import ItemColetado, Matcher


def _montar(con, precos, taxas):
    """precos: {nome: {slug: preco}}; taxas: {slug: taxa}"""
    m = Matcher(con)
    ids = {}
    with transacao(con):
        for slug, t in taxas.items():
            con.execute("UPDATE lojas SET taxa_entrega_estimada=? WHERE slug=?", (t, slug))
        con.execute("UPDATE lojas SET ativo=0 WHERE slug NOT IN (%s)" % ",".join("?" * len(taxas)), list(taxas))
        for i, (nome, por_loja) in enumerate(precos.items()):
            for slug, preco in por_loja.items():
                lj = loja_por_slug(con, slug)
                r = m.resolver(ItemColetado(f"{i}", nome, preco), lj["id_loja"])
                registrar_preco(con, r.id_produto, lj["id_loja"], preco, None, True, f"https://{slug}/{i}", str(i))
                ids[nome] = r.id_produto
            con.execute("INSERT OR REPLACE INTO carrinho VALUES (?, 1)", (ids[nome],))
    return ids


def test_otimizador_considera_frete(con):
    _montar(con, {
        "Arroz Camil 5kg": {"muffato": 25.0, "festval": 27.0, "irani-floresta": 26.0},
        "Feijão Camil 1kg": {"muffato": 9.0, "festval": 7.0, "irani-floresta": 8.9},
        "Café Baggio 500g": {"muffato": 20.0, "festval": 19.5, "irani-floresta": 21.0},
    }, {"muffato": 10.0, "festval": 10.0, "irani-floresta": 5.0})
    plano = otimizador.otimizar(con)
    unica = plano["melhor_unica"]
    assert unica["slug"] == "irani-floresta" and unica["total"] == 60.9  # 55.9 + 5
    div = plano["dividido"]
    # dividir Muffato (arroz) + Festval (feijão, café) = 51.5 + 20 de frete = 71.5 > 60.9: o ótimo é 1 loja
    assert div["total"] == 60.9 and len(div["grupos"]) == 1
    assert div["ingenuo_total"] == 71.5
    assert plano["economia"] == 0


def test_otimizador_divide_quando_compensa(con):
    _montar(con, {
        "Arroz Camil 5kg": {"muffato": 20.0, "festval": 35.0},
        "Café Baggio 500g": {"muffato": 30.0, "festval": 15.0},
    }, {"muffato": 5.0, "festval": 5.0})
    plano = otimizador.otimizar(con)
    assert plano["melhor_unica"]["total"] == 55.0
    assert plano["dividido"]["total"] == 45.0 and plano["economia"] == 10.0


def test_guardas_de_pagamento():
    for t in ["Finalizar pedido", "Confirmar compra", "Pagar", "Comprar agora", "Efetuar pagamento"]:
        assert PROIBIDO_CLICAR.search(t), t
    for t in ["Adicionar", "Entrar", "Ir para o carrinho", "Adicionar ao carrinho"]:
        assert not PROIBIDO_CLICAR.search(t), t
    assert ROTAS_PAGAMENTO.search("https://www.supermuffato.com.br/api/checkout/pub/orderForm/abc/transaction")
    assert not ROTAS_PAGAMENTO.search("https://www.supermuffato.com.br/api/checkout/pub/orderForm/abc/items")


def test_cofre(con):
    from app import cofre
    lj = loja_por_slug(con, "muffato")["id_loja"]
    cofre.salvar(con, lj, "eu@x.com", "segredo", [{"name": "c", "value": "v", "domain": ".x", "path": "/"}])
    bruto = con.execute("SELECT * FROM credenciais_lojas").fetchone()
    assert "segredo" not in "".join(str(v) for v in bruto)
    assert cofre.ler(con, lj)["senha"] == "segredo"
    cofre.salvar(con, lj, senha="nova")   # usuário mantido
    d = cofre.ler(con, lj)
    assert d["usuario"] == "eu@x.com" and d["senha"] == "nova"


@pytest.fixture()
def cliente(cfg, monkeypatch):
    from app import main
    with TestClient(main.app) as c:
        yield c


def test_api_telas_e_carrinho(cliente):
    c = cliente
    from app import main
    con = main.app.state.con
    ids = _montar(con, {"Café Baggio 500g": {"muffato": 20.0, "festval": 19.0}}, {"muffato": 5.0, "festval": 9.0})
    assert c.get("/saude").json()["ok"]
    r = c.get("/?q=baggio")
    assert r.status_code == 200 and "Café Baggio 500g" in r.text and "Menor preço" in r.text
    assert c.get("/?q=baggio", headers={"HX-Request": "true"}).text.lstrip().startswith("<p")
    pid = ids["Café Baggio 500g"]
    assert c.get(f"/produto/{pid}").status_code == 200
    assert c.post(f"/carrinho/{pid}", data={"quantidade": 2}).text == "3"
    plano = c.get("/api/carrinho").json()
    assert plano["melhor_unica"]["slug"] == "muffato"   # 20*3+5=65 < 19*3+9=66
    links = c.get("/api/checkout/links?modo=unica").json()
    assert "checkout/cart/add?sku=0&qty=3" in links[0]["link_carrinho"]
    for p in ["/carrinho", "/coleta", "/watchlist", "/lojas"]:
        assert c.get(p).status_code == 200, p
    r = c.post("/lojas/1/credenciais", data={"usuario": "a", "senha": "b"}, follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    assert "b" not in c.get("/lojas").text.split("Login para o checkout")[1][:200]


def test_login_obrigatorio(cfg, monkeypatch):
    monkeypatch.setattr(cfg, "painel_senha", "abc123")
    from app import main
    with TestClient(main.app) as c:
        assert c.get("/", follow_redirects=False).status_code == 303
        assert c.get("/api/progresso").status_code == 401
        assert c.post("/login", data={"senha": "errada"}, follow_redirects=False).headers["location"] == "/login?erro=1"
        r = c.post("/login", data={"senha": "abc123"}, follow_redirects=False)
        assert r.status_code == 303
        assert c.get("/").status_code == 200
