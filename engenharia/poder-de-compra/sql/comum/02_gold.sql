-- =====================================================================
-- CAMADA GOLD: a tabela que o dashboard lê. Uma linha por ano.
-- "Quanto um salário mínimo compra" em Big Macs, litros de gasolina e m² de aluguel em SP,
-- e o salário em reais do último dezembro com IPCA fechado (deflacionado pelo IPCA).
-- =====================================================================

CREATE OR REPLACE TABLE gold_poder_compra_anual AS
WITH sal AS (
    SELECT YEAR(mes)                 AS ano,
           AVG(salario_minimo)       AS salario_medio,      -- média dos 12 meses (há anos com 2 valores)
           MIN(salario_minimo)       AS salario_min_ano,
           MAX(salario_minimo)       AS salario_max_ano,
           COUNT(*)                  AS meses
    FROM silver_salario_mensal
    WHERE YEAR(mes) >= 1995
    GROUP BY YEAR(mes)
),
inflacao AS (
    -- Índice de preços acumulado: 1995 = 1 x (1+IPCA)...; anos sem IPCA (ano corrente) não deflacionam.
    SELECT s.ano,
           i.ipca_pct,
           EXP(SUM(LN(1 + COALESCE(i.ipca_pct, 0) / 100)) OVER (ORDER BY s.ano
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)) AS indice
    FROM sal s LEFT JOIN silver_ipca_anual i ON i.ano = s.ano
)
SELECT s.ano,
       ROUND(s.salario_medio, 2)                                            AS salario_medio,
       s.salario_min_ano, s.salario_max_ano,
       ROUND(f.ipca_pct, 2)                                                 AS ipca_pct,
       -- deflaciona pelo meio do ano (a média salarial é do ano todo, o índice é de dezembro)
       ROUND(s.salario_medio * MAX(f.indice) OVER ()
             / (f.indice / SQRT(1 + COALESCE(f.ipca_pct, 0) / 100)), 2)    AS salario_real_hoje,
       ROUND(b.bigmac_brl, 2)                                               AS bigmac_preco,
       ROUND(s.salario_medio / b.bigmac_brl, 1)                             AS bigmacs,
       ROUND(s.salario_max_ano / b.bigmac_brl, 1)                           AS bigmacs_maior_salario_ano,
       ROUND(g.gasolina_litro, 3)                                           AS gasolina_litro,
       ROUND(s.salario_medio / g.gasolina_litro, 1)                         AS litros_gasolina,
       ROUND(a.aluguel_m2, 2)                                               AS aluguel_m2_sp,
       ROUND(s.salario_medio / a.aluguel_m2, 1)                             AS m2_aluguel_sp,
       ROUND(50 * a.aluguel_m2, 0)                                          AS aluguel_50m2_sp,
       ROUND(100 * 50 * a.aluguel_m2 / s.salario_medio, 1)                  AS pct_salario_aluguel_50m2
FROM sal s
JOIN inflacao f                  ON f.ano = s.ano
LEFT JOIN silver_bigmac_anual b  ON b.ano = s.ano
LEFT JOIN silver_gasolina_anual g ON g.ano = s.ano
LEFT JOIN silver_aluguel_sp_anual a ON a.ano = s.ano
ORDER BY s.ano;

-- Mês a mês: o que o dashboard usa na tabela da ANP e nos gráficos mensais.
CREATE OR REPLACE TABLE gold_poder_compra_mensal AS
SELECT s.mes,
       s.salario_minimo,
       ROUND(g.gasolina_litro, 3)                      AS gasolina_litro,
       ROUND(s.salario_minimo / g.gasolina_litro, 1)   AS litros_gasolina,
       ROUND(a.aluguel_m2, 2)                          AS aluguel_m2_sp,
       ROUND(s.salario_minimo / a.aluguel_m2, 1)       AS m2_aluguel_sp
FROM silver_salario_mensal s
LEFT JOIN silver_gasolina_mensal g  ON g.mes = s.mes
LEFT JOIN silver_aluguel_sp_mensal a ON a.mes = s.mes
WHERE g.gasolina_litro IS NOT NULL OR a.aluguel_m2 IS NOT NULL
ORDER BY s.mes;

-- Qualidade: uma linha por teste; o pipeline falha se algum "ok" for falso.
CREATE OR REPLACE TABLE gold_qualidade AS
SELECT 'salario 1995-05 = 100'   AS teste,
       (SELECT MAX(salario_minimo) FROM silver_salario_mensal WHERE mes = DATE '1995-05-01') = 100 AS ok
UNION ALL SELECT 'big mac 1995 = 2,45',
       (SELECT bigmac_brl FROM silver_bigmac_anual WHERE ano = 1995) = 2.45
UNION ALL SELECT 'um ano por linha na gold',
       (SELECT COUNT(*) FROM gold_poder_compra_anual) = (SELECT COUNT(DISTINCT ano) FROM gold_poder_compra_anual)
UNION ALL SELECT 'salario sempre positivo',
       (SELECT MIN(salario_medio) FROM gold_poder_compra_anual) > 0;
