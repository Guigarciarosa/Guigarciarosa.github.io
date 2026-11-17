"""Configuração lida do ambiente (.env). Nenhum segredo tem valor padrão."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(nome: str, padrao: bool = False) -> bool:
    v = os.getenv(nome)
    if v is None:
        return padrao
    return v.strip().lower() in {"1", "true", "sim", "yes", "on"}


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("MERCADO_DATA", "./dados")))
    fernet_key: str = field(default_factory=lambda: os.getenv("FERNET_KEY", ""))
    painel_senha: str = field(default_factory=lambda: os.getenv("PAINEL_SENHA", ""))
    cron_hora: int = field(default_factory=lambda: int(os.getenv("COLETA_HORA", "3")))
    cron_minuto: int = field(default_factory=lambda: int(os.getenv("COLETA_MINUTO", "0")))
    agendador: bool = field(default_factory=lambda: _bool("AGENDADOR", True))
    jitter_min: float = field(default_factory=lambda: float(os.getenv("JITTER_MIN", "0.8")))
    jitter_max: float = field(default_factory=lambda: float(os.getenv("JITTER_MAX", "2.4")))
    max_paginas_categoria: int = field(default_factory=lambda: int(os.getenv("MAX_PAGINAS_CATEGORIA", "50")))
    limiar_fuzzy: float = field(default_factory=lambda: float(os.getenv("LIMIAR_FUZZY", "88")))
    checkout_headless: bool = field(default_factory=lambda: _bool("CHECKOUT_HEADLESS", False))
    proxy: str = field(default_factory=lambda: os.getenv("HTTP_PROXY_COLETA", ""))
    # Regionalização VTEX
    muffato_sc: str = field(default_factory=lambda: os.getenv("MUFFATO_SC", "22"))
    festval_sc: str = field(default_factory=lambda: os.getenv("FESTVAL_SC", ""))
    instabuy_api: str = field(default_factory=lambda: os.getenv("INSTABUY_API", "https://api.instabuy.com.br/apiv3"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / "mercado.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media" / "produtos"

    @property
    def perfis_dir(self) -> Path:
        return self.data_dir / "browser_profiles"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    def garantir_pastas(self) -> None:
        for p in (self.db_path.parent, self.media_dir, self.perfis_dir, self.logs_dir):
            p.mkdir(parents=True, exist_ok=True)


_config: Config | None = None


def config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def redefinir(cfg: Config) -> None:
    """Usado nos testes."""
    global _config
    _config = cfg
