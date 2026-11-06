-- =====================================================================
-- CAMADA SILVER: dados limpos, tipados e numa granularidade única (mês ou ano).
-- Este arquivo roda IGUAL no DuckDB (local), no Databricks e no Snowflake:
-- só usa SQL que os três entendem. O que muda entre eles é só a carga da
-- bronze (veja sql/databricks/00_bronze.sql e sql/snowflake/00_bronze.sql).
-- =====================================================================

-- Salário mínimo mês a mês (BCB SGS 1619). Vários anos têm dois valores (ex.: 1995, 2009, 2023).
CREATE OR REPLACE TABLE silver_salario_mensal AS
SELECT CAST(data AS DATE)            AS mes,
       CAST(valor AS DOUBLE)         AS salario_minimo
FROM bronze_salario_minimo
WHERE CAST(data AS DATE) >= DATE '1994-07-01';           -- só depois do Real

-- IPCA anual. Online vem mensal (SGS 433) e compomos os 12 meses;
-- o snapshot já vem acumulado em 12 meses (SGS 13522, dezembro).
CREATE OR REPLACE TABLE silver_ipca_anual AS
SELECT ano, ipca_pct FROM (
    SELECT YEAR(CAST(data AS DATE))                                   AS ano,
           (EXP(SUM(LN(1 + CAST(valor AS DOUBLE) / 100))) - 1) * 100  AS ipca_pct,
           COUNT(*)                                                   AS meses
    FROM bronze_ipca_mensal WHERE tipo = 'mensal'
    GROUP BY YEAR(CAST(data AS DATE))
) m WHERE meses = 12
UNION ALL
SELECT YEAR(CAST(data AS DATE)), CAST(valor AS DOUBLE)
FROM bronze_ipca_mensal WHERE tipo = 'acumulado_12m';

-- Big Mac no Brasil, só em reais (antes de 1995 a moeda era cruzeiro/cruzeiro real).
-- De 2000 em diante a Economist publica duas vezes por ano: usamos a média do ano.
CREATE OR REPLACE TABLE silver_bigmac_anual AS
SELECT YEAR(CAST(date AS DATE))                         AS ano,
       AVG(CAST(local_price AS DOUBLE))                 AS bigmac_brl,
       AVG(CAST(local_price AS DOUBLE) / CAST(dollar_ex AS DOUBLE)) AS bigmac_usd,
       COUNT(*)                                         AS observacoes
FROM bronze_bigmac
WHERE iso_a3 = 'BRA' AND currency_code = 'BRL'
GROUP BY YEAR(CAST(date AS DATE));

-- Gasolina comum, preço médio de revenda Brasil (ANP), mês a mês desde jul/2001.
CREATE OR REPLACE TABLE silver_gasolina_mensal AS
SELECT CAST(mes AS DATE)                        AS mes,
       AVG(CAST(preco_medio_revenda AS DOUBLE)) AS gasolina_litro
FROM bronze_anp_mensal
WHERE UPPER(produto) LIKE 'GASOLINA COMUM%' OR UPPER(produto) = 'GASOLINA C'
GROUP BY CAST(mes AS DATE);

CREATE OR REPLACE TABLE silver_gasolina_anual AS
SELECT YEAR(mes) AS ano, AVG(gasolina_litro) AS gasolina_litro, COUNT(*) AS meses
FROM silver_gasolina_mensal
GROUP BY YEAR(mes);

-- Aluguel residencial em São Paulo, R$/m² (Índice FipeZAP Locação), mês a mês desde jan/2008.
CREATE OR REPLACE TABLE silver_aluguel_sp_mensal AS
SELECT CAST(mes AS DATE)               AS mes,
       AVG(CAST(aluguel_m2 AS DOUBLE)) AS aluguel_m2
FROM bronze_fipezap_locacao
GROUP BY CAST(mes AS DATE);

CREATE OR REPLACE TABLE silver_aluguel_sp_anual AS
SELECT YEAR(mes) AS ano, AVG(aluguel_m2) AS aluguel_m2, COUNT(*) AS meses
FROM silver_aluguel_sp_mensal
GROUP BY YEAR(mes);
