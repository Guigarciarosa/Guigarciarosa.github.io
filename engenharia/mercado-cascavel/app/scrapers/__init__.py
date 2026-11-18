"""Fábrica de coletores por plataforma."""
from __future__ import annotations

from typing import Any

from .base import Coletor, Contexto


def fabricar(loja: Any, params: dict, cliente: Any, ctx: Contexto | None) -> Coletor:
    from .festval import ColetorFestval
    from .instabuy import ColetorInstabuy
    from .irani import ColetorIrani
    from .vtex import ColetorVTEX

    mapa: dict[str, type[Coletor]] = {
        "vtex": ColetorVTEX, "instabuy": ColetorInstabuy, "irani": ColetorIrani, "festval": ColetorFestval,
    }
    cls = mapa.get(loja["plataforma"], Coletor)
    return cls(loja, params, cliente, ctx)
