# Snowflake: como seria

O Snowflake **não tem plano gratuito permanente**. Há um trial de **30 dias com US$ 400 de créditos**, sem cartão. Depois disso a conta é suspensa, então ele não serve para manter um portfólio no ar. Por isso o motor principal é o Databricks Free Edition. Ainda assim, dá para fazer o mesmo projeto no trial para aprender a ferramenta: o SQL de silver e gold é o mesmo.

## Mesmos conceitos, nomes diferentes

| Ideia | Databricks | Snowflake |
|---|---|---|
| Hierarquia | `catálogo.schema.tabela` | `database.schema.tabela` |
| Pasta do dado bruto | Volume | Stage interno (`@bronze_raw`) |
| Ler arquivo | `read_files(...)` | `COPY INTO ... FROM @stage` |
| Motor de SQL | SQL Warehouse serverless | Virtual Warehouse (XS gasta 1 crédito/hora) |
| Versões da tabela | Delta time travel | Time Travel (`AT(OFFSET => -60)`) |
| Linhagem | Unity Catalog Lineage | Horizon / Object dependencies |
| Dashboard nativo | AI/BI Dashboards | Snowsight Dashboards / Streamlit in Snowflake |

## Passo a passo no trial

1. Cadastre-se em **signup.snowflake.com** (edição Standard, qualquer nuvem).
2. Em **Snowsight**, abra uma *SQL Worksheet* e rode `sql/snowflake/00_bronze.sql` (cria database, schema, file format e stage).
3. Envie os CSVs para o stage, pela interface (*Data → Add Data → Load files into a Stage*) ou pelo Snowflake CLI:
   `snow stage copy data/bronze/salario_minimo/salario_minimo.csv @bronze_raw/salario_minimo/`
4. Rode o resto de `00_bronze.sql` (os `COPY INTO`) e depois `sql/comum/01_silver.sql` e `02_gold.sql`, sem mudar nada.
5. Configure o warehouse com `AUTO_SUSPEND = 60` para não queimar crédito à toa.

Prints que valem a pena: o stage com os arquivos, o *Query Profile* da gold e a aba *Time Travel* da tabela.
