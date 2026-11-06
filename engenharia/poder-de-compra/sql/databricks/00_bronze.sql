-- =====================================================================
-- CAMADA BRONZE no Databricks: lê os CSVs enviados para o Volume do Unity Catalog
-- e grava como tabelas Delta, tudo em texto (tipagem é trabalho da silver).
-- ${volume} é trocado pelo pipeline por /Volumes/<catalogo>/<schema>/bronze_raw
-- =====================================================================
CREATE OR REPLACE TABLE bronze_salario_minimo COMMENT 'BCB SGS 1619, mensal' AS
SELECT *, _metadata.file_path AS _arquivo, current_timestamp() AS _carregado_em
FROM read_files('${volume}/salario_minimo/', format => 'csv', header => true, sep => ';', inferSchema => false);

CREATE OR REPLACE TABLE bronze_ipca_mensal COMMENT 'BCB SGS 433 (ou snapshot 13522)' AS
SELECT *, _metadata.file_path AS _arquivo, current_timestamp() AS _carregado_em
FROM read_files('${volume}/ipca_mensal/', format => 'csv', header => true, sep => ';', inferSchema => false);

CREATE OR REPLACE TABLE bronze_bigmac COMMENT 'The Economist big-mac-data (BRA, USA)' AS
SELECT *, _metadata.file_path AS _arquivo, current_timestamp() AS _carregado_em
FROM read_files('${volume}/bigmac/', format => 'csv', header => true, sep => ';', inferSchema => false);

CREATE OR REPLACE TABLE bronze_anp_mensal COMMENT 'ANP, síntese mensal Brasil' AS
SELECT *, _metadata.file_path AS _arquivo, current_timestamp() AS _carregado_em
FROM read_files('${volume}/anp_mensal/', format => 'csv', header => true, sep => ';', inferSchema => false);

CREATE OR REPLACE TABLE bronze_fipezap_locacao COMMENT 'FipeZAP locação, São Paulo, R$/m2' AS
SELECT *, _metadata.file_path AS _arquivo, current_timestamp() AS _carregado_em
FROM read_files('${volume}/fipezap_locacao/', format => 'csv', header => true, sep => ';', inferSchema => false);
