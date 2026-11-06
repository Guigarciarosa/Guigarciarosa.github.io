"""python -m pipeline [--grande]

Extrai (bronze) e transforma. Se as variáveis DATABRICKS_* existirem, roda no Databricks;
senão (ou se o Databricks falhar), roda no DuckDB local. O site sempre recebe um JSON novo.
"""
import argparse
import os

from . import extract, transform

ap = argparse.ArgumentParser()
ap.add_argument("--grande", action="store_true", help="inclui microdados ANP por posto (lento)")
ap.add_argument("--motor", choices=["auto", "duckdb", "databricks"], default="auto")
a = ap.parse_args()

extract.run(a.grande)
usar_dbx = a.motor == "databricks" or (a.motor == "auto" and os.environ.get("DATABRICKS_TOKEN"))
if usar_dbx:
    from . import databricks
    try:
        databricks.run(a.grande)
    except Exception as e:  # noqa: BLE001
        if a.motor == "databricks":
            raise
        print(f"! Databricks falhou:\n{e}\n-> caindo para o DuckDB local")
        transform.run()
else:
    transform.run()
