"""Barramento de progresso e logs para o painel (SSE).

Cada coleta publica eventos {tipo: progresso|log|fim}; o endpoint /api/eventos repassa via SSE.
Logs também vão para a tabela `logs` e para logs/mercado.log (JSON por linha).
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any

from .config import config


@dataclass
class Progresso:
    slug: str
    nome: str
    status: str = "parado"     # parado | rodando | ok | erro | parcial
    lidos: int = 0
    total: int = 0
    total_estimado: bool = True
    novos: int = 0
    unificados: int = 0
    precos_atualizados: int = 0
    erros: int = 0
    bloqueios: int = 0
    inicio: str | None = None
    fim: str | None = None
    mensagem: str = ""

    @property
    def pct(self) -> int:
        return int(100 * self.lidos / self.total) if self.total else 0

    def como_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["pct"] = self.pct
        return d


@dataclass
class Barramento:
    progresso: dict[str, Progresso] = field(default_factory=dict)
    _assinantes: set[asyncio.Queue] = field(default_factory=set)
    _loop: asyncio.AbstractEventLoop | None = None
    _con: sqlite3.Connection | None = None
    _arquivo: logging.Logger | None = None

    def ligar(self, con: sqlite3.Connection, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._con = con
        self._loop = loop
        lg = logging.getLogger("mercado.arquivo")
        if not lg.handlers:
            try:
                config().logs_dir.mkdir(parents=True, exist_ok=True)
                h = RotatingFileHandler(config().logs_dir / "mercado.log", maxBytes=5_000_000, backupCount=5,
                                        encoding="utf-8")
                h.setFormatter(logging.Formatter("%(message)s"))
                lg.addHandler(h)
                lg.setLevel(logging.INFO)
                lg.propagate = False
            except OSError:
                pass
        self._arquivo = lg

    def assinar(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._assinantes.add(q)
        return q

    def cancelar(self, q: asyncio.Queue) -> None:
        self._assinantes.discard(q)

    def _publicar(self, evento: dict[str, Any]) -> None:
        for q in list(self._assinantes):
            try:
                if self._loop and self._loop.is_running():
                    self._loop.call_soon_threadsafe(_por, q, evento)
                else:
                    _por(q, evento)
            except RuntimeError:
                self._assinantes.discard(q)

    def p(self, slug: str, nome: str | None = None) -> Progresso:
        if slug not in self.progresso:
            self.progresso[slug] = Progresso(slug=slug, nome=nome or slug)
        return self.progresso[slug]

    def atualizar(self, prog: Progresso) -> None:
        self._publicar({"tipo": "progresso", "dados": prog.como_dict()})

    def log(self, nivel: str, origem: str, mensagem: str) -> None:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if self._con is not None:
            from .database import log as log_db
            try:
                log_db(self._con, nivel, origem, mensagem)
            except sqlite3.Error:
                pass
        if self._arquivo:
            self._arquivo.info(json.dumps({"ts": ts, "nivel": nivel, "origem": origem, "msg": mensagem},
                                          ensure_ascii=False))
        logging.getLogger("mercado").log(getattr(logging, nivel, logging.INFO), "[%s] %s", origem, mensagem)
        self._publicar({"tipo": "log", "dados": {"ts": ts, "nivel": nivel, "origem": origem, "mensagem": mensagem}})


def _por(q: asyncio.Queue, evento: dict) -> None:
    try:
        q.put_nowait(evento)
    except asyncio.QueueFull:
        try:
            q.get_nowait()
            q.put_nowait(evento)
        except (asyncio.QueueEmpty, asyncio.QueueFull):
            pass


barramento = Barramento()
