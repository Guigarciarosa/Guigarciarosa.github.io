# Fontes e metodologia

| Dado | Fonte | Endpoint usado | Observação |
|---|---|---|---|
| Salário mínimo mensal | Banco Central, SGS série **1619** | `api.bcb.gov.br/dados/serie/bcdata.sgs.1619/dados` | A API devolve no máximo 10 anos por chamada; o extrator pede em janelas |
| IPCA mensal | Banco Central, SGS série **433** | `api.bcb.gov.br/dados/serie/bcdata.sgs.433/dados` | Composto mês a mês na silver; snapshot usa a série 13522 (12 meses) |
| Big Mac | The Economist, `big-mac-data` no GitHub | `source-data/big-mac-historical-source-data.csv` (1986-1999) e `big-mac-source-data-v2.csv` (2000+) | Brasil em BRL desde abr/1995 |
| Gasolina | ANP, Levantamento de Preços de Combustíveis | síntese mensal Brasil (`shlp/mensal/...xlsx`) e microdados por posto (`shpc/dsas/ca/ca-AAAA-SS.csv`) | Microdados só com `--grande` |
| Aluguel SP | Fipe, Índice FipeZAP Locação | `downloads.fipe.org.br/indices/fipezap/fipezap-serieshistoricas.xlsx` | Preço anunciado, R$/m², desde 2008 |
| Salário mínimo real | IPEA, série `GAC12_SALMINRE12` | `ipeadata.gov.br/api/odata4` | Usado para conferir o deflator |

## Valores conferidos à mão

Ficam em `data/seed/referencias_verificadas.csv` e viram teste no `pytest`:

- Big Mac abr/1995: R$ 2,45 (Economist, arquivo histórico)
- Mínimo abr/1995 R$ 70; mai/1995 R$ 100; mai/2004 R$ 260; fev/2009 R$ 465 (BCB 1619)
- Gasolina 2004: R$ 2,137/l (ANP, citado na checagem da AFP de 09/03/2021)
- Aluguel SP ago/2026: R$ 65,36/m² (informe FipeZAP de agosto/2026)

## Projetos de referência

- [`TheEconomist/big-mac-data`](https://github.com/TheEconomist/big-mac-data): a fonte e o método do índice.
- [`wilsonfreitas/python-bcb`](https://github.com/wilsonfreitas/python-bcb): cliente Python do SGS; aqui a chamada é feita direto com `requests` para não depender dele.
- [`Guigarciarosa/quanto-compra-o-minimo`](https://github.com/Guigarciarosa/quanto-compra-o-minimo): projeto anterior (RAG + Text-to-SQL) de onde vieram os snapshots e a tabela de normas do salário mínimo.
