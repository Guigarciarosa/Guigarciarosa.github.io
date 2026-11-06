# Projeto 1: qual o real valor de compra do brasileiro?

> Pergunta: quanto o salário mínimo realmente compra, ano a ano, de 1995 até hoje?

Em vez de responder só com "o salário real subiu X%", o projeto mede o mínimo em três coisas concretas:

| "Moeda" | Fonte | Desde |
|---|---|---|
| Big Mac | The Economist, Big Mac Index | 1995 (em reais) |
| Litro de gasolina comum | ANP, levantamento de preços | 2001 (série mensal) |
| m² de aluguel residencial em São Paulo | Índice FipeZAP Locação | 2008 |
| Salário real (R$ de hoje) | IPCA, BCB SGS 433 | 1995 |

## As três perguntas do briefing

**"Em 1995 se compravam 40 Big Macs com o salário mínimo."** Confere, com uma ressalva. Na edição de abril de 1995 da Economist o Big Mac custava **R$ 2,45**. O mínimo era R$ 70 até abril e passou a **R$ 100 em maio** (Lei 9.032/1995). Com R$ 100 dava **40,8 Big Macs**; com R$ 70, 28,6. Na média do ano (R$ 90), 36,7.

**"Em 2009 o aluguel médio em São Paulo era de x mil."** O FipeZAP mede preço anunciado por m², não por imóvel. O pipeline baixa a planilha oficial da Fipe e calcula o R$/m² médio de 2009, o aluguel de um apartamento de 50 m² e quanto isso representava do mínimo (R$ 460,83 na média de 2009). Para comparação, em agosto de 2026 o m² em SP estava em R$ 65,36.

**"Um litro de gasolina custava em média x em 2004."** **R$ 2,137** (média anual da ANP para a gasolina comum). Com o mínimo médio de 2004 (R$ 253,33) dava uns 119 litros.

## Pipeline

```
fontes oficiais ──► bronze (como veio) ──► silver (limpo, um grão) ──► gold (pronto pro gráfico) ──► site/data/poder_compra.json
   Python             Volume + Delta          SQL comum                   SQL comum + testes
```

```bash
python -m pipeline              # extrai e transforma (Databricks se houver .env, senão DuckDB)
python -m pipeline --grande     # + microdados ANP por posto (milhões de linhas)
pytest -q                       # testes, inclusive contra os valores conferidos à mão
```

Arquivos principais:

- `pipeline/extract.py`: um extrator por fonte, com retentativa, manifesto (origem, linhas, hash) e snapshot de reserva.
- `sql/comum/01_silver.sql` e `02_gold.sql`: o mesmo SQL para DuckDB, Databricks e Snowflake.
- `sql/databricks/00_bronze.sql`: carga do Volume com `read_files`.
- `sql/databricks/03_gold_postos.sql`: a parte "big data" (preço por posto, UF e mês).
- `data/seed/referencias_verificadas.csv`: números conferidos nas fontes, usados como teste.

## Decisões de modelagem

- **Salário do ano = média dos 12 meses.** Vários anos tiveram dois valores (1995, 2009, 2011, 2020, 2023). A gold também guarda o maior valor do ano (`bigmacs_maior_salario_ano`), que é o que responde "depois do reajuste".
- **Big Mac só em BRL.** Antes de 1995 a Economist registra cruzeiro e cruzeiro real; essas linhas ficam na bronze e saem na silver.
- **IPCA composto mês a mês**, e o salário médio do ano é deflacionado pelo meio do ano. O resultado sai em R$ do último dezembro com IPCA fechado.
- **Ausência é ausência.** Ano sem dado fica vazio no gráfico; nada é interpolado.

## Limitações

- Big Mac é um item só, com preço de rede. Serve como régua, não como cesta.
- FipeZAP é preço **anunciado**, não de contrato.
- Gasolina é média nacional; a carga `--grande` abre por UF.
