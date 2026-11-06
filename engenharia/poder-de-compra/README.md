# Projeto 1 · Poder de compra do salário mínimo (engenharia)

Pipeline que alimenta o dashboard em `/projetos/poder-de-compra/`.

```
pipeline/   extract.py (bronze) · transform.py (DuckDB) · databricks.py (API REST) · __main__.py
sql/        comum/ (silver + gold, roda igual em DuckDB, Databricks e Snowflake) · databricks/ · snowflake/
data/       snapshots/ (reserva offline) · seed/ (valores conferidos à mão, usados nos testes)
tests/      pytest
```

```bash
pip install -r requirements.txt
SITE_DATA=../../data python -m pipeline          # Databricks se houver .env, senão DuckDB
pytest -q
```

Documentação: [docs.html?doc=projeto-1](../../docs.html?doc=projeto-1) e [guia do Databricks](../../docs.html?doc=databricks).
