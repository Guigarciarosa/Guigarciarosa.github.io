-- =====================================================================
-- CARGA GRANDE (opcional, --grande): microdados de preço por posto da ANP, 2004 em diante.
-- Milhões de linhas: é aqui que um warehouse em nuvem faz diferença de verdade.
-- =====================================================================
CREATE OR REPLACE TABLE bronze_anp_postos AS
SELECT * FROM read_files('${volume}/anp_postos/', format => 'parquet');

CREATE OR REPLACE TABLE silver_anp_postos AS
SELECT estado_sigla                                              AS uf,
       municipio,
       UPPER(TRIM(produto))                                      AS produto,
       TO_DATE(data_da_coleta, 'dd/MM/yyyy')                      AS data_coleta,
       CAST(REPLACE(valor_de_venda, ',', '.') AS DOUBLE)         AS preco_venda,
       bandeira
FROM bronze_anp_postos
WHERE valor_de_venda IS NOT NULL;

-- Preço médio, mínimo e máximo da gasolina por UF e mês
CREATE OR REPLACE TABLE gold_gasolina_uf_mensal AS
SELECT uf, DATE_TRUNC('MONTH', data_coleta) AS mes,
       ROUND(AVG(preco_venda), 3) AS preco_medio,
       ROUND(PERCENTILE(preco_venda, 0.5), 3) AS preco_mediano,
       MIN(preco_venda) AS preco_min, MAX(preco_venda) AS preco_max,
       COUNT(*) AS coletas
FROM silver_anp_postos
WHERE produto LIKE 'GASOLINA%' AND produto NOT LIKE '%ADITIVADA%'
GROUP BY uf, DATE_TRUNC('MONTH', data_coleta);
