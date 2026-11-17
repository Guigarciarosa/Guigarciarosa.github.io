"""Normalização de nomes de produto e extração estrita de peso/volume/quantidade.

Ex.: "Café Torrado e Moído BAGGIO Gourmet 2x500g" ->
     Medida(pacote=2, valor=500, unidade="g"), nome "baggio cafe gourmet moido torrado 2x500g"
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_UNIDADES = {
    # massa -> g
    "kg": ("g", 1000.0), "kilo": ("g", 1000.0), "kilos": ("g", 1000.0), "quilo": ("g", 1000.0),
    "quilos": ("g", 1000.0), "g": ("g", 1.0), "gr": ("g", 1.0), "grs": ("g", 1.0), "grama": ("g", 1.0),
    "gramas": ("g", 1.0), "mg": ("g", 0.001),
    # volume -> ml
    "l": ("ml", 1000.0), "lt": ("ml", 1000.0), "lts": ("ml", 1000.0), "litro": ("ml", 1000.0),
    "litros": ("ml", 1000.0), "ml": ("ml", 1.0), "cl": ("ml", 10.0),
    # comprimento -> m
    "m": ("m", 1.0), "mt": ("m", 1.0), "mts": ("m", 1.0), "metro": ("m", 1.0), "metros": ("m", 1.0),
}
_CONTAGEM = r"(?:un|und|unid|unidade|unidades|rolos?|pcs?|pecas?|capsulas?|saches?|sachets?|latas?|garrafas?|fraldas?|envelopes?|tabletes?|folhas?)"
_NUM = r"(\d+(?:[.,]\d+)?)"
_UN = r"(kg|kilos?|quilos?|grs?|gramas?|g|mg|litros?|lts?|lt|l|ml|cl|metros?|mts?|mt|m)"

_RE_MULTI = re.compile(rf"\b(\d+)\s*[x×]\s*{_NUM}\s*{_UN}\b")           # 2x500g, 6 x 1 l
_RE_MEDIDA = re.compile(rf"\b{_NUM}\s*{_UN}\b")                          # 500g, 1,5 l
_RE_CONT = re.compile(rf"\b(?:c/?\s*|com\s+|leve\s+)?(\d+)\s*{_CONTAGEM}\b")  # 12 rolos, c/6 un
_RE_CONT_PRE = re.compile(rf"\b(?:kit|pack|caixa|cx|fardo|pacote)\s*(?:com\s+|c/\s*)?(\d+)\b")

PALAVRAS_VAZIAS = {
    "de", "da", "do", "das", "dos", "com", "c", "e", "em", "para", "p", "sabor", "tipo", "pct", "pacote",
    "embalagem", "emb", "un", "und", "unid", "unidade", "a", "o", "ao", "na", "no", "sache", "frasco",
    "pote", "lata", "garrafa", "caixa", "cx", "bandeja", "kit", "leve", "pague",
    # enchimento comum de nome de gôndola que não muda o produto
    "torrado", "moido", "tp", "vd", "vidro", "pet", "sc", "pc", "pouch", "stand", "up",
}

# Descritores que mudam o produto: se um lado tem e o outro não (ou têm diferentes), não é o mesmo item.
DESCRITORES_CONFLITANTES = {
    "integral", "desnatado", "semidesnatado", "zero", "diet", "light", "lactose", "tradicional",
    "descafeinado", "extraforte", "forte", "suave", "gourmet", "organico", "premium", "refil",
    "concentrado", "liquido", "po", "neutro", "limao", "coco", "morango", "chocolate", "baunilha",
    "uva", "laranja", "maca", "original", "classico", "fit", "gelado", "fatiado", "ralado",
}


@dataclass(frozen=True)
class Medida:
    pacote: int = 1
    valor: float | None = None   # na unidade-base
    unidade: str | None = None   # g | ml | m | un

    @property
    def total(self) -> float | None:
        return None if self.valor is None else self.pacote * self.valor

    def rotulo(self) -> str:
        if self.valor is None:
            return f"{self.pacote}un" if self.pacote > 1 else ""
        v = self.valor
        un = self.unidade
        if un == "g" and v >= 1000 and v % 1000 == 0:
            v, un = v / 1000, "kg"
        elif un == "ml" and v >= 1000 and v % 1000 == 0:
            v, un = v / 1000, "l"
        txt = f"{_fmt(v)}{un}"
        return f"{self.pacote}x{txt}" if self.pacote > 1 else txt


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def sem_acento(txt: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", txt) if not unicodedata.combining(c))


def limpar(txt: str) -> str:
    t = sem_acento(txt or "").lower()
    t = t.replace("×", "x")
    t = re.sub(r"(\d),(\d)", r"\1.\2", t)
    t = re.sub(r"[^a-z0-9.x/ ]+", " ", t)
    t = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def extrair_medida(nome: str) -> Medida:
    t = limpar(nome)
    m = _RE_MULTI.search(t)
    if m:
        un, fator = _UNIDADES[m.group(3)]
        return Medida(int(m.group(1)), round(float(m.group(2)) * fator, 3), un)
    medidas = list(_RE_MEDIDA.finditer(t))
    # descarta "m" isolado sem número colado quando há medida melhor (ex.: "30m" de papel é válido)
    pacote = 1
    c = _RE_CONT.search(t) or _RE_CONT_PRE.search(t)
    if c:
        pacote = max(1, int(c.group(1)))
    if medidas:
        # prefere massa/volume a comprimento
        medidas.sort(key=lambda x: _UNIDADES[x.group(2)][0] == "m")
        mm = medidas[0]
        un, fator = _UNIDADES[mm.group(2)]
        return Medida(pacote, round(float(mm.group(1)) * fator, 3), un)
    if c:
        return Medida(pacote, 1.0, "un")
    return Medida()


def tokens_nome(nome: str) -> list[str]:
    """Tokens descritivos, sem medidas nem palavras vazias."""
    t = limpar(nome)
    t = _RE_MULTI.sub(" ", t)
    t = _RE_MEDIDA.sub(" ", t)
    t = _RE_CONT.sub(" ", t)
    t = _RE_CONT_PRE.sub(" ", t)
    t = t.replace("/", " ")
    return [w for w in t.split() if w not in PALAVRAS_VAZIAS and len(w) > 1 or w.isdigit()]


def nome_normalizado(nome: str, marca: str | None = None) -> str:
    """marca + item + medida, com tokens ordenados para ser estável entre lojas."""
    toks = tokens_nome(nome)
    marca_toks = tokens_nome(marca) if marca else []
    corpo = sorted({w for w in toks if w not in marca_toks})
    med = extrair_medida(nome).rotulo()
    partes = marca_toks + corpo + ([med] if med else [])
    return " ".join(partes).strip()


def normalizar_ean(ean: str | int | None) -> str | None:
    """Mantém só GTIN-8/12/13/14 com dígito verificador válido; devolve em 13/14 dígitos."""
    if ean is None:
        return None
    d = re.sub(r"\D", "", str(ean))
    if len(d) not in (8, 12, 13, 14) or set(d) == {"0"}:
        return None
    if not _gtin_valido(d):
        return None
    d = d.lstrip("0")
    return d.zfill(13) if len(d) <= 13 else d


def _gtin_valido(d: str) -> bool:
    corpo, dv = d[:-1], int(d[-1])
    soma = sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(reversed(corpo)))
    return (10 - soma % 10) % 10 == dv


def descritores(tokens: list[str] | set[str]) -> set[str]:
    return {t for t in tokens if t in DESCRITORES_CONFLITANTES}


def numeros(tokens: list[str] | set[str]) -> set[str]:
    return {t for t in tokens if any(ch.isdigit() for ch in t)}
