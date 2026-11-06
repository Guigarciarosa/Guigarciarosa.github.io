"""Camada BRONZE: baixa cada fonte oficial e grava o dado como veio, mais um manifesto.

Cada extrator tenta a fonte online; se a rede ou o formato falhar, usa o snapshot versionado
em data/snapshots/ e registra isso no manifesto (campo "origem"). Assim o dashboard nunca
fica vazio, e dá para saber exatamente de onde veio cada número.

Uso:
    python -m pipeline.extract            # fontes leves (segundos)
    python -m pipeline.extract --grande   # + microdados ANP por posto (milhões de linhas)
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
BRONZE = ROOT / "data" / "bronze"
SNAP = ROOT / "data" / "snapshots"
UA: dict = {}  # a ANP recusa alguns User-Agents personalizados; usa o padrão do requests

# ---------------------------------------------------------------- fontes
BCB_SGS = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados?formato=json&dataInicial={ini}&dataFinal={fim}"
SGS = {"salario_minimo": 1619, "ipca_mensal": 433}

BIGMAC_V2 = "https://raw.githubusercontent.com/TheEconomist/big-mac-data/master/source-data/big-mac-source-data-v2.csv"
BIGMAC_HIST = "https://raw.githubusercontent.com/TheEconomist/big-mac-data/master/source-data/big-mac-historical-source-data.csv"

_ANP = "https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/precos/precos-revenda-e-de-distribuicao-combustiveis/shlp/mensal/"
ANP_MENSAL = [_ANP.replace("/mensal/", "/2001-2012/") + "mensal-brasil-2001-a-2012.xlsx",
              _ANP + "mensal-brasil-desde-jan2013.xlsx"]
# Microdados semestrais por posto (gasolina, etanol, diesel...), 2004 em diante: a "carga grande".
ANP_POSTOS = "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/arquivos/shpc/dsas/ca/ca-{ano}-{sem:02d}.csv"

FIPEZAP = "https://downloads.fipe.org.br/indices/fipezap/fipezap-serieshistoricas.xlsx"
IPEA = "http://www.ipeadata.gov.br/api/odata4/ValoresSerie(SERCODIGO='{codigo}')"
IPEA_SERIES = {"salario_minimo_real": "GAC12_SALMINRE12"}


# ---------------------------------------------------------------- utilidades
def _get(url: str, timeout: int = 90, tentativas: int = 3) -> bytes:
    erro: Exception | None = None
    for i in range(tentativas):
        try:
            r = requests.get(url, headers=UA, timeout=timeout)
            r.raise_for_status()
            return r.content
        except requests.RequestException as e:  # noqa: PERF203
            erro = e
            time.sleep(2 ** (i + 1))
    raise erro  # type: ignore[misc]


def _salvar(df: pd.DataFrame, nome: str, origem: str, url: str, manifesto: list) -> None:
    pasta = BRONZE / nome
    pasta.mkdir(parents=True, exist_ok=True)
    arq = pasta / f"{nome}.csv"
    df.to_csv(arq, index=False, sep=";")
    manifesto.append({
        "tabela": nome, "arquivo": str(arq.relative_to(ROOT)), "linhas": len(df),
        "origem": origem, "url": url, "sha256": hashlib.sha256(arq.read_bytes()).hexdigest()[:16],
        "extraido_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    print(f"  bronze/{nome}: {len(df):>9,} linhas  ({origem})")


# ---------------------------------------------------------------- extratores
def bcb(nome: str, manifesto: list) -> None:
    """SGS/BCB. A API limita a 10 anos por chamada, então pede em janelas."""
    codigo = SGS[nome]
    try:
        partes = []
        for ini in range(1994, datetime.now().year + 1, 10):
            url = BCB_SGS.format(codigo=codigo, ini=f"01/01/{ini}", fim=f"31/12/{ini + 9}")
            partes += json.loads(_get(url))
        df = pd.DataFrame(partes).drop_duplicates("data")
        df["data"] = pd.to_datetime(df["data"], dayfirst=True).dt.strftime("%Y-%m-%d")
        df["valor"] = pd.to_numeric(df["valor"])
        df["tipo"] = "mensal"
        _salvar(df, nome, "online", BCB_SGS.format(codigo=codigo, ini="...", fim="..."), manifesto)
    except Exception as e:  # noqa: BLE001
        print(f"  ! {nome}: {type(e).__name__}: {e!s:.120}; usando snapshot")
        if nome == "salario_minimo":
            s = pd.read_csv(SNAP / "salario_minimo.csv", sep=";")
            # expande vigências em meses
            s["inicio"] = pd.to_datetime(s["vigencia"])
            meses = pd.date_range("1994-07-01", pd.Timestamp.today().normalize(), freq="MS")
            df = pd.DataFrame({"data": meses})
            df = pd.merge_asof(df, s.sort_values("inicio"), left_on="data", right_on="inicio")
            df = pd.DataFrame({"data": df["data"].dt.strftime("%Y-%m-%d"), "valor": df["valor"], "tipo": "mensal"})
        else:
            s = pd.read_csv(SNAP / "ipca_12m_dezembro.csv", sep=";")
            df = pd.DataFrame({"data": s["ano"].astype(str) + "-12-01", "valor": s["ipca_pct"],
                               "tipo": "acumulado_12m"})  # SGS 13522, só dezembro
        _salvar(df, nome, "snapshot", "data/snapshots", manifesto)


def bigmac(manifesto: list) -> None:
    """Economist: arquivo histórico (1986-1999) + v2 (2000 em diante). Guarda só o Brasil e os EUA."""
    try:
        hist = pd.read_csv(io.BytesIO(_get(BIGMAC_HIST).replace(b"\r", b"\n")))
        v2 = pd.read_csv(io.BytesIO(_get(BIGMAC_V2)))
        origem = "online"
    except Exception as e:  # noqa: BLE001
        print(f"  ! bigmac: {type(e).__name__}: {e!s:.120}; usando snapshot")
        hist = pd.read_csv(SNAP / "bigmac_economist_historico.csv")
        v2 = pd.read_csv(SNAP / "bigmac_economist_v2.csv")
        origem = "snapshot"
    cols = ["name", "iso_a3", "currency_code", "local_price", "dollar_ex", "date"]
    df = pd.concat([hist[cols].assign(arquivo="historico"), v2[cols].assign(arquivo="v2")])
    df["date"] = pd.to_datetime(df["date"].str[:10]).dt.strftime("%Y-%m-%d")
    df = df[df["iso_a3"].isin(["BRA", "USA"])].drop_duplicates(["iso_a3", "date"], keep="last")
    _salvar(df, "bigmac", origem, BIGMAC_V2, manifesto)


def _sem_acento(t) -> str:
    return unicodedata.normalize("NFKD", str(t)).encode("ascii", "ignore").decode().upper().strip()


def _ler_planilha_anp(conteudo: bytes) -> pd.DataFrame:
    """As planilhas da ANP têm 10-16 linhas de título e observações: o cabeçalho é a linha cuja
    1ª coluna é 'MÊS'. A grafia muda entre arquivos ('PRECO' x 'PREÇO'), então normaliza acentos."""
    bruto = pd.read_excel(io.BytesIO(conteudo), header=None)
    linha = bruto.index[bruto.iloc[:, 0].map(_sem_acento).isin(["MES", "MES/ANO"])][0]
    df = bruto.iloc[linha + 1:].copy()
    df.columns = [_sem_acento(c) for c in bruto.iloc[linha]]
    return df.dropna(how="all")


def anp_mensal(manifesto: list) -> None:
    try:
        df = pd.concat([_ler_planilha_anp(_get(u)) for u in ANP_MENSAL], ignore_index=True)
        col_preco = next(c for c in df.columns if "PRECO MEDIO REVENDA" in c)
        df = pd.DataFrame({
            "mes": pd.to_datetime(df["MES"], errors="coerce").dt.strftime("%Y-%m-01"),
            "produto": df["PRODUTO"].map(_sem_acento),
            "preco_medio_revenda": pd.to_numeric(df[col_preco], errors="coerce"),
        }).dropna()
        df = df[df["produto"].str.startswith("GASOLINA")]
        if df.empty:
            raise ValueError("nenhuma linha de gasolina nas planilhas da ANP")
        _salvar(df, "anp_mensal", "online", ANP_MENSAL[1], manifesto)
    except Exception as e:  # noqa: BLE001
        print(f"  ! anp_mensal: {type(e).__name__}: {e!s:.120}; usando snapshot (Anuário ANP, média das UFs, 2016-2025)")
        s = pd.read_csv(SNAP / "gasolina_c_anp_uf.csv", sep=";")
        df = s.groupby("ano", as_index=False)["preco_litro"].mean()
        df = pd.DataFrame({"mes": df["ano"].astype(str) + "-07-01", "produto": "GASOLINA COMUM",
                           "preco_medio_revenda": df["preco_litro"].round(3)})
        _salvar(df, "anp_mensal", "snapshot", "data/snapshots/gasolina_c_anp_uf.csv", manifesto)


def anp_postos(manifesto: list, anos: range) -> None:
    """Microdados por posto: ~500 mil linhas por semestre. Grava em Parquet particionado por ano."""
    pasta = BRONZE / "anp_postos"
    pasta.mkdir(parents=True, exist_ok=True)
    total = 0
    for ano in anos:
        for sem in (1, 2):
            destino = pasta / f"ano={ano}" / f"sem={sem}.parquet"
            if destino.exists():
                continue
            try:
                raw = _get(ANP_POSTOS.format(ano=ano, sem=sem), timeout=300)
            except Exception as e:  # noqa: BLE001
                print(f"  ! anp_postos {ano}-{sem}: {e!s:.60}")
                continue
            df = pd.read_csv(io.BytesIO(raw), sep=";", encoding="latin-1", dtype=str)
            df.columns = [c.strip().lower().replace(" - ", "_").replace(" ", "_") for c in df.columns]
            destino.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(destino, index=False)
            total += len(df)
            print(f"  bronze/anp_postos/{ano}-{sem}: {len(df):,} linhas")
    manifesto.append({"tabela": "anp_postos", "arquivo": "data/bronze/anp_postos/", "linhas_novas": total,
                      "origem": "online", "url": ANP_POSTOS,
                      "extraido_em": datetime.now(timezone.utc).isoformat(timespec="seconds")})


def _fipezap_sp(conteudo: bytes) -> pd.DataFrame:
    """Aba 'São Paulo': cabeçalho em várias linhas com células mescladas
    (Imóveis residenciais > Locação > Preço médio (R$/m²) > Total) e datas a partir de jan/2008.
    Ausentes vêm como '.'."""
    xl = pd.ExcelFile(io.BytesIO(conteudo))
    aba = next(a for a in xl.sheet_names if _sem_acento(a) == "SAO PAULO")
    bruto = xl.parse(aba, header=None)
    eh_data = bruto.map(lambda v: isinstance(v, (datetime, pd.Timestamp)))
    col_data = int(eh_data.sum().idxmax())                           # coluna com mais datas
    primeira = int(eh_data[col_data].idxmax())                       # 1ª linha de dados
    cab = bruto.iloc[:primeira].ffill(axis=1).fillna("").map(_sem_acento)
    rotulo = cab.agg(" | ".join)                                     # um rótulo por coluna
    alvo = [i for i, t in enumerate(rotulo)
            if "RESIDENC" in t and "LOCACAO" in t and "PRECO MEDIO" in t and "TOTAL" in t]
    if len(alvo) != 1:
        raise ValueError(f"coluna de aluguel R$/m2 não encontrada (candidatas: {alvo})")
    dados = bruto.iloc[primeira:]
    df = pd.DataFrame({"mes": pd.to_datetime(dados[col_data], errors="coerce"),
                       "aluguel_m2": pd.to_numeric(dados[alvo[0]], errors="coerce")}).dropna()
    df["mes"] = df["mes"].dt.strftime("%Y-%m-01")
    df["cidade"] = "São Paulo"
    return df


def fipezap(manifesto: list) -> None:
    """Índice FipeZAP: preço médio de locação residencial (R$/m²) em São Paulo, desde jan/2008."""
    try:
        _salvar(_fipezap_sp(_get(FIPEZAP)), "fipezap_locacao", "online", FIPEZAP, manifesto)
    except Exception as e:  # noqa: BLE001
        print(f"  ! fipezap: {type(e).__name__}: {e!s:.120}; usando só o ponto verificado (ago/2026)")
        df = pd.DataFrame({"mes": ["2026-08-01"], "aluguel_m2": [65.36], "cidade": ["São Paulo"]})
        _salvar(df, "fipezap_locacao", "seed", "data/seed/referencias_verificadas.csv", manifesto)


def ipea(manifesto: list) -> None:
    for nome, codigo in IPEA_SERIES.items():
        try:
            dados = json.loads(_get(IPEA.format(codigo=codigo)))["value"]
            df = pd.DataFrame(dados)[["VALDATA", "VALVALOR"]]
            df.columns = ["data", "valor"]
            df["data"] = df["data"].str[:10]
            _salvar(df, f"ipea_{nome}", "online", IPEA.format(codigo=codigo), manifesto)
        except Exception as e:  # noqa: BLE001
            print(f"  ! ipea {nome}: {e!s:.80}; pulando (série opcional)")


def run(grande: bool = False) -> list[dict]:
    manifesto: list[dict] = []
    print("Extraindo fontes (bronze)...")
    bcb("salario_minimo", manifesto)
    bcb("ipca_mensal", manifesto)
    bigmac(manifesto)
    anp_mensal(manifesto)
    fipezap(manifesto)
    ipea(manifesto)
    if grande:
        anp_postos(manifesto, range(2004, datetime.now().year + 1))
    BRONZE.mkdir(parents=True, exist_ok=True)
    (BRONZE / "_manifesto.json").write_text(json.dumps(manifesto, ensure_ascii=False, indent=2))
    return manifesto


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--grande", action="store_true", help="baixa também os microdados ANP por posto")
    run(ap.parse_args().grande)
