# Guia do Databricks (Free Edition)

> Por que Databricks e não Snowflake? O Snowflake não tem plano grátis permanente: só um trial de 30 dias com US$ 400 em créditos, e depois a conta é suspensa. O **Databricks Free Edition** é grátis sem prazo (uso pessoal e de estudo), então o portfólio continua funcionando. O SQL do projeto roda nos dois; veja [Snowflake: como seria](docs.html?doc=snowflake).

## 1. Criar a conta

1. Acesse **databricks.com/learn/free-edition** e cadastre-se (Google ou e-mail).
2. Ao entrar, você cai num workspace com **Unity Catalog** e um catálogo chamado `workspace`.
3. Limites da Free Edition: só computação serverless, um SQL warehouse pequeno, cota diária de uso. Para este projeto sobra.

## 2. Pegar as 3 credenciais

| Variável | Onde achar |
|---|---|
| `DATABRICKS_HOST` | A URL do navegador até `.com`, ex. `https://dbc-1234abcd-5678.cloud.databricks.com` |
| `DATABRICKS_TOKEN` | Avatar (canto superior direito) → **Settings** → **Developer** → **Access tokens** → *Generate new token* (validade de 90 dias) |
| `DATABRICKS_WAREHOUSE_ID` | Menu **SQL Warehouses** → *Serverless Starter Warehouse* → aba **Connection details** → o final do *HTTP path* (`/sql/1.0/warehouses/<ID>`) |

Coloque no arquivo `.env` do servidor (nunca no Git):

```env
DATABRICKS_HOST=https://dbc-....cloud.databricks.com
DATABRICKS_TOKEN=dapi....
DATABRICKS_WAREHOUSE_ID=....
DATABRICKS_CATALOG=workspace
DATABRICKS_SCHEMA=poder_compra
```

## 3. Rodar

```bash
docker-compose run --rm pipeline              # extrai e roda no Databricks
docker-compose run --rm pipeline --grande     # + microdados por posto
```

O que o script faz, na ordem (tudo pela API REST, sem instalar nada do Databricks):

1. `CREATE SCHEMA workspace.poder_compra` e `CREATE VOLUME bronze_raw`
2. envia cada CSV para `/Volumes/workspace/poder_compra/bronze_raw/...` (Files API)
3. cria as tabelas Delta bronze com `read_files(...)`
4. roda `sql/comum/01_silver.sql` e `02_gold.sql` (SQL Statement Execution API)
5. lê `gold_poder_compra_anual` e grava o JSON que o dashboard usa

Se faltar o `.env` ou o Databricks falhar, o pipeline cai para o DuckDB local e o site continua de pé. O selo no topo do dashboard mostra qual motor gerou os dados.

## 4. O que olhar no Databricks (e printar)

| Onde | O que mostra | Print sugerido |
|---|---|---|
| **Catalog** → workspace → poder_compra | As tabelas bronze/silver/gold e o volume | `01-catalogo.png` |
| Volume `bronze_raw` | Os arquivos brutos que subiram | `02-volume.png` |
| Tabela `gold_poder_compra_anual` → **Lineage** | O grafo bronze → silver → gold, montado sozinho pelo Unity Catalog | `03-lineage.png` |
| **SQL Editor** | Rode `SELECT * FROM workspace.poder_compra.gold_poder_compra_anual` | `04-sql-editor.png` |
| **Query History** | Cada comando que o pipeline mandou, com tempo e linhas | `05-query-history.png` |
| **Dashboards** (AI/BI) | Crie um dashboard nativo em cima da gold, para comparar com o web | `06-dashboard-dbx.png` |
| Tabela → **History** | Versões Delta: cada rodada do pipeline é uma versão (time travel) | `07-delta-history.png` |

Para o print aparecer no portfólio: salve em `site/assets/prints/` e adicione uma linha em `site/assets/prints/prints.json`:

```json
[
  {"projeto": "poder-de-compra", "ferramenta": "Databricks", "arquivo": "01-catalogo.png",
   "legenda": "Schema poder_compra com as camadas bronze, silver e gold"}
]
```

## 5. Conceitos em uma frase

- **Unity Catalog**: o "dono" de tudo. Organiza em `catálogo.schema.tabela` e controla permissão e linhagem.
- **Volume**: uma pasta de arquivos governada pelo catálogo. É onde fica o dado bruto.
- **Delta Lake**: o formato das tabelas. Parquet + log de transações, o que dá `UPDATE`, `MERGE` e *time travel* (`SELECT ... VERSION AS OF 3`).
- **SQL Warehouse serverless**: o motor que executa SQL. Liga sozinho quando chega uma query e desliga quando fica parado.
- **`read_files`**: lê CSV, JSON ou Parquet direto do Volume, sem criar tabela externa antes.

## Para estudar depois

- Trocar a carga completa por `MERGE INTO` (incremental) na bronze dos microdados.
- Transformar os SQLs num **Job** agendado dentro do próprio Databricks.
- Usar **DLT / Lakeflow Declarative Pipelines** para declarar as camadas com expectativas de qualidade.
