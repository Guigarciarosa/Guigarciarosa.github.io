"""Popula um banco de DEMONSTRAÇÃO (preços fictícios) para prints e testes de interface.
Uso: MERCADO_DATA=/tmp/demo python scripts/demo.py   (nunca aponte para o banco real)"""
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
assert "AppData" not in os.getenv("MERCADO_DATA", ""), "não rode a demo no banco real"
from app.config import config  # noqa: E402
from app.database import conectar, inicializar, loja_por_slug, registrar_preco, transacao  # noqa: E402
from app.matcher import ItemColetado, Matcher  # noqa: E402

random.seed(7)
config().garantir_pastas()
con = inicializar(conectar())
CATALOGO = [
    ("Café Torrado e Moído Baggio Gourmet 500g", "Baggio", "Café", 24.9),
    ("Café Pilão Tradicional 500g", "Pilão", "Café", 18.9),
    ("Arroz Branco Tio João Tipo 1 5kg", "Tio João", "Mercearia", 29.9),
    ("Feijão Carioca Camil 1kg", "Camil", "Mercearia", 8.49),
    ("Detergente Líquido Ypê Neutro 500ml", "Ypê", "Limpeza", 2.79),
    ("Leite UHT Integral Piracanjuba 1L", "Piracanjuba", "Laticínios", 5.49),
    ("Azeite Extra Virgem Gallo 500ml", "Gallo", "Mercearia", 39.9),
    ("Papel Higiênico Neve Folha Dupla 12 rolos 30m", "Neve", "Higiene", 22.9),
    ("Sabão em Pó Omo Lavagem Perfeita 1,6kg", "Omo", "Limpeza", 27.9),
    ("Cerveja Heineken Lata 350ml", "Heineken", "Bebidas", 5.29),
    ("Açúcar Refinado União 1kg", "União", "Mercearia", 4.99),
    ("Chocolate ao Leite Lacta 80g", "Lacta", "Doces", 6.49),
]
LOJAS = ["muffato", "muffatao", "festval", "irani-parque-verde", "irani-floresta", "irani-pacaembu", "irani-alto-alegre"]
m = Matcher(con)
with transacao(con):
    for i, (nome, marca, cat, base) in enumerate(CATALOGO):
        for slug in random.sample(LOJAS, random.randint(3, 6)):
            lj = loja_por_slug(con, slug)
            variacao = nome.upper() if slug.startswith("irani") else nome
            r = m.resolver(ItemColetado(f"{slug}-{i}", variacao, base, marca=marca, categoria=cat), lj["id_loja"])
            preco = round(base * random.uniform(0.86, 1.14), 2)
            promo = round(preco * 0.85, 2) if random.random() < 0.2 else None
            registrar_preco(con, r.id_produto, lj["id_loja"], preco, promo, True, f"{lj['url_base']}/p/{i}", f"{slug}-{i}")
        if i < 6:
            con.execute("INSERT OR REPLACE INTO carrinho VALUES (?, ?)", (r.id_produto, 1 + i % 3))
print("demo pronta em", config().data_dir)
