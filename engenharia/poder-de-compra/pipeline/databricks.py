"""Roda o pipeline no Databricks (Free Edition) usando só a API REST.

O que acontece, na ordem:
  1. cria schema e volume no Unity Catalog         (CREATE SCHEMA / CREATE VOLUME)
  2. envia os arquivos bronze para o volume       (Files API: PUT /api/2.0/fs/files/Volumes/...)
  3. cria as tabelas Delta bronze com read_files  (sql/databricks/00_bronze.sql)
  4. roda silver e gold                            (sql/comum/*.sql, os mesmos do DuckDB)
  5. opcional: gold dos microdados ANP por posto  (sql/databricks/03_gold_postos.sql, com --grande)
  6. lê a gold e grava site/data/poder_compra.json com motor="databricks"

Variáveis de ambiente (arquivo .env, nunca versionado):
  DATABRICKS_HOST=https://dbc-xxxxxxxx-xxxx.cloud.databricks.com
  DATABRICKS_TOKEN=dapi...                      (User Settings > Developer > Access tokens)
  DATABRICKS_WAREHOUSE_ID=xxxxxxxxxxxxxxxx      (opcional: sem ele, usa o primeiro warehouse da conta)
  DATABRICKS_CATALOG=workspace                  (padrão da Free Edition)
  DATABRICKS_SCHEMA=poder_compra
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import requests

from .transform import BRONZE, ROOT, SQL_COMUM, TABELAS_BRONZE, salvar_json, statements

SQL_DBX = ROOT / "sql" / "databricks"


class Databricks:
    def __init__(self) -> None:
        self.host = os.environ["DATABRICKS_HOST"].rstrip("/")
        self.wh = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
        self.catalog = os.environ.get("DATABRICKS_CATALOG", "workspace")
        self.schema = os.environ.get("DATABRICKS_SCHEMA", "poder_compra")
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {os.environ['DATABRICKS_TOKEN']}"
        self.volume = f"/Volumes/{self.catalog}/{self.schema}/bronze_raw"
        if not self.wh:  # sem ID: usa o primeiro SQL warehouse da conta (na Free Edition só existe um)
            whs = self.s.get(f"{self.host}/api/2.0/sql/warehouses", timeout=60).json().get("warehouses", [])
            if not whs:
                raise RuntimeError("Nenhum SQL warehouse encontrado no workspace")
            self.wh = whs[0]["id"]
            print(f"  warehouse: {whs[0]['name']} ({self.wh})")

    # ---- SQL Statement Execution API
    def sql(self, statement: str, schema: bool = True) -> list[dict]:
        body = {"warehouse_id": self.wh, "statement": statement, "wait_timeout": "50s",
                "catalog": self.catalog, "format": "JSON_ARRAY", "disposition": "INLINE"}
        if schema:
            body["schema"] = self.schema
        r = self.s.post(f"{self.host}/api/2.0/sql/statements", json=body, timeout=90)
        r.raise_for_status()
        res = r.json()
        while res["status"]["state"] in ("PENDING", "RUNNING"):
            time.sleep(3)  # warehouse serverless "acordando"
            res = self.s.get(f"{self.host}/api/2.0/sql/statements/{res['statement_id']}", timeout=60).json()
        if res["status"]["state"] != "SUCCEEDED":
            erro = res["status"].get("error", {}).get("message", res["status"])
            raise RuntimeError(f"{erro}\n--- SQL ---\n{statement[:600]}")
        if "manifest" not in res or not res.get("result", {}).get("data_array"):
            return []
        cols = res["manifest"]["schema"]["columns"]
        return [{c["name"]: _tipar(v, c["type_name"]) for c, v in zip(cols, linha)}
                for linha in res["result"]["data_array"]]

    # ---- Files API
    def upload(self, local: Path, remoto: str) -> None:
        with open(local, "rb") as f:
            r = self.s.put(f"{self.host}/api/2.0/fs/files{remoto}", params={"overwrite": "true"}, data=f, timeout=600)
        r.raise_for_status()

    def rodar_arquivo(self, path: Path) -> None:
        for n, st in enumerate(statements(path), 1):
            try:
                self.sql(st.replace("${volume}", self.volume))
            except Exception as e:
                raise RuntimeError(f"{path.name}, comando {n}: {e}") from e
        print(f"  {path.relative_to(ROOT)} ok")


def _tipar(v, tipo: str):
    if v is None:
        return None
    if tipo in ("INT", "LONG", "SHORT", "BYTE", "BIGINT", "SMALLINT", "TINYINT"):
        return int(v)
    if tipo in ("DOUBLE", "FLOAT", "DECIMAL"):
        return float(v)
    if tipo == "BOOLEAN":
        return v == "true"
    return v


def run(grande: bool = False) -> dict:
    db = Databricks()
    print(f"Databricks: {db.host}  catálogo={db.catalog}  schema={db.schema}")
    db.sql(f"CREATE SCHEMA IF NOT EXISTS {db.catalog}.{db.schema} COMMENT 'Portfólio: poder de compra do salário mínimo'", schema=False)
    db.sql(f"CREATE VOLUME IF NOT EXISTS {db.catalog}.{db.schema}.bronze_raw COMMENT 'Arquivos brutos (camada bronze)'")

    print("Enviando bronze para o volume...")
    for t in TABELAS_BRONZE:
        db.upload(BRONZE / t / f"{t}.csv", f"{db.volume}/{t}/{t}.csv")
        print(f"  {db.volume}/{t}/{t}.csv")
    if grande:
        for pq in sorted((BRONZE / "anp_postos").rglob("*.parquet")):
            db.upload(pq, f"{db.volume}/anp_postos/{pq.relative_to(BRONZE / 'anp_postos')}")
        print("  microdados ANP enviados")

    print("Transformando no Databricks...")
    db.rodar_arquivo(SQL_DBX / "00_bronze.sql")
    for f in SQL_COMUM:
        db.rodar_arquivo(f)
    if grande:
        db.rodar_arquivo(SQL_DBX / "03_gold_postos.sql")

    anual = db.sql("SELECT * FROM gold_poder_compra_anual ORDER BY ano")
    mensal = db.sql("SELECT * FROM gold_poder_compra_mensal ORDER BY mes")
    qualidade = db.sql("SELECT * FROM gold_qualidade")
    return salvar_json(anual, qualidade, "databricks", mensal=mensal)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--grande", action="store_true", help="envia e processa os microdados ANP por posto")
    run(ap.parse_args().grande)
