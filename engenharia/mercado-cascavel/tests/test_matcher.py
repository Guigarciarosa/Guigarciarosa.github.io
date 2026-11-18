import pytest

from app import normalizar as nz
from app.database import registrar_preco
from app.matcher import ItemColetado, Matcher


@pytest.mark.parametrize("nome,esperado", [
    ("Café Torrado e Moído Baggio Gourmet 500g", (1, 500.0, "g")),
    ("Café Baggio 1kg", (1, 1000.0, "g")),
    ("Café Baggio 2x500g", (2, 500.0, "g")),
    ("Refrigerante Coca-Cola 1,5L", (1, 1500.0, "ml")),
    ("Cerveja Heineken Lata 350ml c/ 12 un", (12, 350.0, "ml")),
    ("Papel Higiênico Neve 12 rolos 30m", (12, 30.0, "m")),
    ("Ovos Brancos Caixa com 30", (30, 1.0, "un")),
    ("Banana Prata", (1, None, None)),
])
def test_extrair_medida(nome, esperado):
    m = nz.extrair_medida(nome)
    assert (m.pacote, m.valor, m.unidade) == esperado


def test_ean():
    assert nz.normalizar_ean("7896348301489") == "7896348301489"
    assert nz.normalizar_ean("07896348301489") == "7896348301489"   # GTIN-14 com zero à esquerda
    assert nz.normalizar_ean("7896348301488") is None                 # dígito verificador errado
    assert nz.normalizar_ean("1311") is None                          # PLU de balança
    assert nz.normalizar_ean("0000000000000") is None


def test_nome_normalizado_estavel_entre_lojas():
    a = nz.nome_normalizado("CAFÉ BAGGIO GOURMET 500G", "Baggio")
    b = nz.nome_normalizado("Café Gourmet Baggio 500 g", "BAGGIO")
    assert a == b == "baggio cafe gourmet 500g"


def item(sku, nome, preco=10.0, ean=None, marca=None):
    return ItemColetado(sku=sku, nome=nome, preco=preco, ean=ean, marca=marca)


def test_ean_unifica_mesmo_com_nome_diferente(con):
    m = Matcher(con)
    a = m.resolver(item("A1", "Café Solúvel Melitta Lata 150g", ean="7896348301489"), 1)
    b = m.resolver(item("B9", "MELITTA SOLUVEL 150 GR", ean="07896348301489"), 2)
    assert a.criado and b.metodo == "ean" and b.id_produto == a.id_produto


def test_fuzzy_unifica_variacao_de_nome(con):
    m = Matcher(con)
    a = m.resolver(item("1", "Café Torrado e Moído Baggio Gourmet 500g", marca="Baggio"), 1)
    b = m.resolver(item("2", "CAFE BAGGIO GOURMET 500G", marca="BAGGIO"), 3)
    assert b.metodo == "fuzzy" and b.id_produto == a.id_produto and b.score >= 88


@pytest.mark.parametrize("outro", [
    "Café Torrado e Moído Baggio Gourmet 1kg",      # peso diferente: 500g != 1kg
    "Café Baggio Gourmet 2x500g",                   # pacote diferente
    "Café Baggio Gourmet Descafeinado 500g",        # descritor a mais
    "Café Melitta Gourmet 500g",                    # marca diferente
])
def test_fuzzy_respeita_travas(con, outro):
    m = Matcher(con)
    a = m.resolver(item("1", "Café Torrado e Moído Baggio Gourmet 500g", marca="Baggio"), 1)
    marca = "Melitta" if "Melitta" in outro else "Baggio"
    b = m.resolver(item("2", outro, marca=marca), 3)
    assert b.criado and b.id_produto != a.id_produto


def test_sabores_e_tipos_nao_misturam(con):
    m = Matcher(con)
    a = m.resolver(item("1", "Detergente Líquido Ypê Neutro 500ml", marca="Ypê"), 1)
    b = m.resolver(item("2", "Detergente Líquido Ypê Limão 500ml", marca="Ypê"), 3)
    c = m.resolver(item("3", "Leite UHT Integral Piracanjuba 1L", marca="Piracanjuba"), 1)
    d = m.resolver(item("4", "Leite UHT Desnatado Piracanjuba 1L", marca="Piracanjuba"), 3)
    assert a.id_produto != b.id_produto and c.id_produto != d.id_produto


def test_eans_diferentes_nunca_unificam(con):
    m = Matcher(con)
    a = m.resolver(item("1", "Arroz Tio João Tipo 1 5kg", ean="7893500020110"), 1)
    b = m.resolver(item("2", "Arroz Tio João Tipo 1 5kg", ean="7893500024859"), 3)
    assert a.id_produto != b.id_produto


def test_sku_e_chave_de_rede(con):
    m = Matcher(con)
    a = m.resolver(ItemColetado("194314", "Biscoito Isabela Tortinhas 140g", 3.29, chave_cruzada="mercafacil:194314"), 3)
    b = m.resolver(ItemColetado("194314", "Biscoito Isabela Tortinhas 140g Choc", 3.29, chave_cruzada="mercafacil:194314"), 4)
    c = m.resolver(ItemColetado("194314", "qualquer nome", 3.49), 3)
    assert b.metodo == "rede" and b.id_produto == a.id_produto
    assert c.metodo == "sku" and c.id_produto == a.id_produto


def test_indice_carrega_do_banco(con):
    Matcher(con).resolver(item("1", "Açúcar Refinado União 1kg", ean="7891910000197"), 1)
    m2 = Matcher(con)  # nova coleta: índice relido do SQLite
    r = m2.resolver(item("x", "ACUCAR UNIAO REFINADO 1KG"), 5)
    assert r.metodo == "fuzzy"


def test_historico_so_grava_quando_muda(con):
    m = Matcher(con)
    r = m.resolver(item("1", "Feijão Carioca Camil 1kg"), 1)
    assert registrar_preco(con, r.id_produto, 1, 8.99, None, True, "u", "1") is True
    assert registrar_preco(con, r.id_produto, 1, 8.99, None, True, "u", "1") is False
    assert registrar_preco(con, r.id_produto, 1, 8.99, 7.49, True, "u", "1") is True
    linhas = con.execute("SELECT COUNT(*) FROM historico_precos").fetchone()[0]
    atual = con.execute("SELECT preco_efetivo FROM precos_atuais WHERE id_produto=?", (r.id_produto,)).fetchone()[0]
    assert linhas == 2 and float(atual) == 7.49
