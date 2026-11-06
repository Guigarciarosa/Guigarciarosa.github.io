-- =====================================================================
-- Variante SNOWFLAKE (trial de 30 dias): mesma ideia da bronze do Databricks.
-- No Snowflake o "Volume" vira um STAGE interno e o read_files vira COPY INTO.
-- Depois rode sql/comum/01_silver.sql e 02_gold.sql sem mudar nada.
-- Envie os arquivos antes, pelo SnowSQL/Snowflake CLI:
--   snow stage copy data/bronze/salario_minimo/salario_minimo.csv @bronze_raw/salario_minimo/
-- =====================================================================
CREATE DATABASE IF NOT EXISTS PORTFOLIO;
CREATE SCHEMA IF NOT EXISTS PORTFOLIO.PODER_COMPRA;
USE SCHEMA PORTFOLIO.PODER_COMPRA;

CREATE OR REPLACE FILE FORMAT csv_pv TYPE = CSV FIELD_DELIMITER = ';' PARSE_HEADER = TRUE
  FIELD_OPTIONALLY_ENCLOSED_BY = '"' EMPTY_FIELD_AS_NULL = TRUE;
CREATE STAGE IF NOT EXISTS bronze_raw FILE_FORMAT = csv_pv;

-- INFER_SCHEMA + USING TEMPLATE cria a tabela com as colunas do cabeçalho
CREATE OR REPLACE TABLE bronze_salario_minimo USING TEMPLATE (
  SELECT ARRAY_AGG(OBJECT_CONSTRUCT(*)) FROM TABLE(INFER_SCHEMA(LOCATION => '@bronze_raw/salario_minimo/', FILE_FORMAT => 'csv_pv')));
COPY INTO bronze_salario_minimo FROM @bronze_raw/salario_minimo/ FILE_FORMAT = csv_pv MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE;

CREATE OR REPLACE TABLE bronze_ipca_mensal USING TEMPLATE (
  SELECT ARRAY_AGG(OBJECT_CONSTRUCT(*)) FROM TABLE(INFER_SCHEMA(LOCATION => '@bronze_raw/ipca_mensal/', FILE_FORMAT => 'csv_pv')));
COPY INTO bronze_ipca_mensal FROM @bronze_raw/ipca_mensal/ FILE_FORMAT = csv_pv MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE;

CREATE OR REPLACE TABLE bronze_bigmac USING TEMPLATE (
  SELECT ARRAY_AGG(OBJECT_CONSTRUCT(*)) FROM TABLE(INFER_SCHEMA(LOCATION => '@bronze_raw/bigmac/', FILE_FORMAT => 'csv_pv')));
COPY INTO bronze_bigmac FROM @bronze_raw/bigmac/ FILE_FORMAT = csv_pv MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE;

CREATE OR REPLACE TABLE bronze_anp_mensal USING TEMPLATE (
  SELECT ARRAY_AGG(OBJECT_CONSTRUCT(*)) FROM TABLE(INFER_SCHEMA(LOCATION => '@bronze_raw/anp_mensal/', FILE_FORMAT => 'csv_pv')));
COPY INTO bronze_anp_mensal FROM @bronze_raw/anp_mensal/ FILE_FORMAT = csv_pv MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE;

CREATE OR REPLACE TABLE bronze_fipezap_locacao USING TEMPLATE (
  SELECT ARRAY_AGG(OBJECT_CONSTRUCT(*)) FROM TABLE(INFER_SCHEMA(LOCATION => '@bronze_raw/fipezap_locacao/', FILE_FORMAT => 'csv_pv')));
COPY INTO bronze_fipezap_locacao FROM @bronze_raw/fipezap_locacao/ FILE_FORMAT = csv_pv MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE;
