import csv
from pathlib import Path

import duckdb
import pytest

from pipeline import extract, transform

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def gold(tmp_path_factory):
    extract.run()
    db = tmp_path_factory.mktemp("w") / "t.duckdb"
    payload = transform.run(db)
    return {r["ano"]: r for r in payload["anual"]}, duckdb.connect(str(db))


def test_qualidade_ok(gold):
    _, con = gold
    assert all(ok for _, ok in con.execute("select * from gold_qualidade").fetchall())


def test_1995_bigmacs(gold):
    anual, _ = gold
    # abr/1995: Big Mac R$ 2,45; a partir de mai/1995 o mínimo foi a R$ 100 -> 40,8 Big Macs
    assert anual[1995]["bigmacs_maior_salario_ano"] == pytest.approx(40.8, abs=0.1)
    assert anual[1995]["bigmacs"] == pytest.approx(36.7, abs=0.1)   # média do ano (R$ 90)


def test_referencias_verificadas(gold):
    anual, con = gold
    refs = list(csv.DictReader(open(ROOT / "data/seed/referencias_verificadas.csv"), delimiter=";"))
    for r in refs:
        if r["indicador"] == "salario_minimo":
            v = con.execute("select salario_minimo from silver_salario_mensal where mes = ?",
                            [r["periodo"] + "-01"]).fetchone()[0]
            assert v == pytest.approx(float(r["valor"]))
        if r["indicador"] == "gasolina_litro" and anual[int(r["periodo"])]["gasolina_litro"]:
            # só checa quando a série online da ANP estiver presente
            assert anual[int(r["periodo"])]["gasolina_litro"] == pytest.approx(float(r["valor"]), rel=0.03)


def test_sql_portavel():
    """O SQL comum não pode usar sintaxe exclusiva do DuckDB (roda no Databricks e no Snowflake)."""
    for f in (ROOT / "sql/comum").glob("*.sql"):
        txt = f.read_text()
        for proibido in ("::", "ASOF", "read_csv", "QUALIFY", "list_"):
            assert proibido not in txt, f"{f.name} usa {proibido}"


def test_separador_sql_respeita_strings(tmp_path):
    from pipeline.transform import statements
    f = tmp_path / "x.sql"
    f.write_text("-- comentario; com ponto e virgula\nSELECT 'a;b' AS x; -- fim; aqui\nSELECT 'it''s' AS y;\n")
    assert statements(f) == ["SELECT 'a;b' AS x", "SELECT 'it''s' AS y"]
