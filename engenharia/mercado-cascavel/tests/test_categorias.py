import re

import pytest
from fastapi.testclient import TestClient

from app.categorias import classificar, reclassificar
from app.database import loja_por_slug, registrar_preco, transacao
from app.matcher import ItemColetado, Matcher


@pytest.mark.parametrize("nome, loja_cat, esperado", [
    ("Vinho Tinto Casillero del Diablo Cabernet Sauvignon 750ml", None, ("Vinho", "Chileno")),
    ("Vinho Chileno Gato Negro Merlot 750ml", None, ("Vinho", "Chileno")),
    ("Vinho Argentino Trapiche Malbec 750ml", None, ("Vinho", "Argentino")),
    ("Vinho Tinto Fino Seco Pisano Tannat 750ml", None, ("Vinho", "Uruguaio")),
    ("Vinho Português Periquita Tinto 750ml", None, ("Vinho", "Português")),
    ("Vinho Verde Casal Garcia 750ml", None, ("Vinho", "Português")),
    ("Malbec Reserva 750ml", "Vinhos Argentinos", ("Vinho", "Argentino")),
    ("Vinagre de Vinho Tinto Castelo 750ml", None, (None, None)),
    ("Detergente Líquido Ypê Neutro 500ml", None, ("Limpeza", "Detergente")),
    ("Lava Louças Limpol Cristal 500ml", None, ("Limpeza", "Detergente")),
    ("Lava Roupas Líquido Omo 3L", None, ("Limpeza", "Sabão de lavar roupa")),
    ("Sabão em Pó Brilhante 800g", None, ("Limpeza", "Sabão de lavar roupa")),
    ("Sabonete Dove 90g", None, (None, None)),
    ("Arroz Tio João 5kg", None, (None, None)),
])
def test_classificar(nome, loja_cat, esperado):
    assert classificar(nome, loja_cat) == esperado


NOMES = ["Vinho Tinto Casillero del Diablo Cabernet Sauvignon 750ml", "Vinho Argentino Trapiche Malbec 750ml",
         "Vinho Tinto Pisano Tannat 750ml", "Vinho Português Periquita 750ml", "Detergente Ypê Neutro 500ml",
         "Sabão em Pó Omo 800g", "Arroz Tio João 5kg"]


@pytest.fixture()
def cliente(cfg):
    from app import main
    with TestClient(main.app) as c:
        con = main.app.state.con
        m, lj = Matcher(con), loja_por_slug(con, "muffato")
        with transacao(con):
            for i, n in enumerate(NOMES):
                r = m.resolver(ItemColetado(str(i), n, 10.0 + i), lj["id_loja"])
                registrar_preco(con, r.id_produto, lj["id_loja"], 10.0 + i, None, True, f"https://x/{i}", str(i))
        yield c


def _nomes(html):
    return {n for n in NOMES if n in html}


def test_busca_e_filtro_por_categoria(cliente):
    h = {"HX-Request": "true"}
    assert _nomes(cliente.get("/?q=vinho chileno", headers=h).text) == {NOMES[0]}
    assert _nomes(cliente.get("/?q=uruguai", headers=h).text) == {NOMES[2]}
    assert _nomes(cliente.get("/?q=portugues", headers=h).text) == {NOMES[3]}
    assert _nomes(cliente.get("/?q=limpeza", headers=h).text) == {NOMES[4], NOMES[5]}
    assert _nomes(cliente.get("/?q=sabao de lavar roupa", headers=h).text) == {NOMES[5]}
    assert _nomes(cliente.get("/?categoria=Vinho", headers=h).text) == set(NOMES[:4])
    assert _nomes(cliente.get("/?categoria=Vinho|Argentino", headers=h).text) == {NOMES[1]}
    assert _nomes(cliente.get("/?categoria=Limpeza|Detergente", headers=h).text) == {NOMES[4]}
    tela = cliente.get("/").text
    assert '<optgroup label="Vinho">' in tela and "Chileno (1)" in tela


def test_reclassificar_banco_antigo(cliente):
    from app import main
    con = main.app.state.con
    con.execute("UPDATE produtos_mestres SET categoria_curada=NULL, subcategoria=NULL, tags_busca=NULL")
    assert reclassificar(con) == 6 and reclassificar(con) == 0


def test_paginacao_sem_parametros_vazios(cliente, monkeypatch):
    from app import consultas
    monkeypatch.setattr(consultas, "POR_PAGINA", 2)
    h = {"HX-Request": "true"}
    # o que o link antigo mandava: so_promo vazio -> 422 e a grade não trocava
    assert cliente.get("/?q=&categoria=&loja=&ordem=relevancia&so_promo=&pagina=2", headers=h).status_code == 200
    r = cliente.get("/?categoria=Vinho&ordem=nome", headers=h)
    links = re.findall(r'hx-get="([^"]+)"', r.text)
    prox = [l for l in links if "pagina=" in l]
    assert prox == ["/?categoria=Vinho&amp;ordem=nome&amp;pagina=2"]
    p2 = cliente.get(prox[0].replace("&amp;", "&"), headers=h).text
    assert "página 2 de 2" in p2 and "← anterior" in p2 and "próxima" not in p2
    vistos = _nomes(cliente.get("/?categoria=Vinho&ordem=nome", headers=h).text) | _nomes(p2)
    assert vistos == set(NOMES[:4])
