"""Roda silver + gold no DuckDB local e exporta o JSON que o dashboard lê.

É o "motor reserva": funciona sem conta em nuvem. Quando o Databricks estiver configurado,
`python -m pipeline.databricks` roda os MESMOS arquivos SQL lá e exporta o mesmo JSON.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
BRONZE = ROOT / "data" / "bronze"
SQL_COMUM = sorted((ROOT / "sql" / "comum").glob("*.sql"))
SITE_DATA = Path(os.environ.get("SITE_DATA", ROOT / "site" / "data"))  # no repo do site: ../../data
TABELAS_BRONZE = ["salario_minimo", "ipca_mensal", "bigmac", "anp_mensal", "fipezap_locacao"]


def statements(path: Path) -> list[str]:
    """Separa um arquivo .sql em comandos.

    Respeita strings ('...') e comentários (-- ...): um ';' dentro de sep => ';' ou de um
    comentário não quebra o comando. Comentários são removidos antes de enviar.
    """
    txt, cmds, atual, i, n = path.read_text(), [], [], 0, None
    n = len(txt)
    while i < n:
        c = txt[i]
        if c == "'":                                   # string: copia até o fecho ('' é aspas escapadas)
            j = i + 1
            while j < n and not (txt[j] == "'" and txt[j + 1:j + 2] != "'"):
                j += 2 if txt[j] == "'" else 1
            atual.append(txt[i:j + 1]); i = j + 1
        elif txt.startswith("--", i):                  # comentário de linha: pula
            j = txt.find("\n", i); i = n if j < 0 else j
        elif c == ";":
            cmds.append("".join(atual).strip()); atual = []; i += 1
        else:
            atual.append(c); i += 1
    cmds.append("".join(atual).strip())
    return [c for c in cmds if c]


def carregar_bronze(con: duckdb.DuckDBPyConnection) -> None:
    for t in TABELAS_BRONZE:
        arq = BRONZE / t / f"{t}.csv"
        con.execute(f"CREATE OR REPLACE TABLE bronze_{t} AS SELECT * FROM read_csv('{arq}', delim=';', header=true, all_varchar=true)")


def exportar(con, motor: str, destino: Path = SITE_DATA) -> dict:
    """Gold -> site/data/poder_compra.json (também usado pelo módulo do Databricks)."""
    def linhas(sql):
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    anual = linhas("SELECT * FROM gold_poder_compra_anual ORDER BY ano")
    mensal = linhas("SELECT * FROM gold_poder_compra_mensal ORDER BY mes")
    return salvar_json(anual, linhas("SELECT * FROM gold_qualidade"), motor, destino, mensal)


def salvar_json(anual: list[dict], qualidade: list[dict], motor: str, destino: Path = SITE_DATA,
                mensal: list[dict] | None = None) -> dict:
    manifesto_arq = BRONZE / "_manifesto.json"
    manifesto = json.loads(manifesto_arq.read_text()) if manifesto_arq.exists() else []
    falhas = [q["teste"] for q in qualidade if str(q["ok"]).lower() not in ("true", "1")]
    if falhas:
        raise SystemExit(f"Teste de qualidade falhou: {falhas}")
    payload = {
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "motor": motor,
        "fontes": manifesto,
        "qualidade": qualidade,
        "anual": anual,
        "mensal": [{**m, "mes": str(m["mes"])[:7]} for m in (mensal or [])],
    }
    destino.mkdir(parents=True, exist_ok=True)
    (destino / "poder_compra.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str))
    print(f"  site/data/poder_compra.json: {len(anual)} anos, motor={motor}")
    return payload


def run(db_path: Path | str = ROOT / "data" / "warehouse.duckdb") -> dict:
    con = duckdb.connect(str(db_path))
    print("Transformando no DuckDB (silver -> gold)...")
    carregar_bronze(con)
    for f in SQL_COMUM:
        for s in statements(f):
            con.execute(s)
        print(f"  {f.name} ok")
    return exportar(con, "duckdb")


if __name__ == "__main__":
    run()
