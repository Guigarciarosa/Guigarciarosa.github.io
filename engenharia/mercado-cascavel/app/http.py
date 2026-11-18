"""Cliente HTTP "educado": jitter aleatório, rotação de User-Agent, backoff exponencial em 429/503.

Usa curl_cffi (impersona TLS/HTTP2 do Chrome) quando instalado; senão httpx.
"""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Any, Callable

from .config import config

log = logging.getLogger("mercado.http")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
]

try:  # pragma: no cover - depende do ambiente
    from curl_cffi.requests import AsyncSession as _CurlSession  # type: ignore
except Exception:  # noqa: BLE001
    _CurlSession = None

import httpx


class Bloqueado(RuntimeError):
    """403/429 persistentes: WAF ou rate-limit."""


@dataclass
class Resposta:
    status: int
    headers: dict[str, str]
    texto: str
    conteudo: bytes
    url: str

    def json(self) -> Any:
        import json
        return json.loads(self.texto)


class ClienteEducado:
    def __init__(self, base_headers: dict[str, str] | None = None, concorrencia: int = 2,
                 tentativas: int = 5, ao_bloqueio: Callable[[str], None] | None = None,
                 dormir: Callable[[float], Any] | None = None, transporte: Any = None):
        cfg = config()
        self.jmin, self.jmax = cfg.jitter_min, cfg.jitter_max
        self.tentativas = tentativas
        self._sem = asyncio.Semaphore(concorrencia)
        self._ao_bloqueio = ao_bloqueio
        self._dormir = dormir or asyncio.sleep
        self._ua = random.choice(USER_AGENTS)
        self.headers = {
            "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.6",
            **(base_headers or {}),
        }
        self._proxy = cfg.proxy or None
        self._curl = None
        if transporte is not None:  # testes: httpx.MockTransport
            self._httpx = httpx.AsyncClient(transport=transporte, follow_redirects=True, timeout=30)
        elif _CurlSession is not None:
            self._curl = _CurlSession(impersonate="chrome", timeout=30,
                                      proxies={"https": self._proxy, "http": self._proxy} if self._proxy else None)
            self._httpx = None
        else:
            self._httpx = httpx.AsyncClient(follow_redirects=True, timeout=30, proxy=self._proxy, http2=False)

    async def __aenter__(self) -> "ClienteEducado":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.fechar()

    async def fechar(self) -> None:
        if self._curl is not None:
            await self._curl.close()
        if self._httpx is not None:
            await self._httpx.aclose()

    def trocar_ua(self) -> None:
        self._ua = random.choice([u for u in USER_AGENTS if u != self._ua])

    async def get(self, url: str, params: dict | None = None, headers: dict | None = None,
                  jitter: bool = True, aceitar: tuple[int, ...] = (200,)) -> Resposta:
        espera = 2.0
        ultimo: Exception | None = None
        for tentativa in range(1, self.tentativas + 1):
            async with self._sem:
                if jitter:
                    await self._dormir(random.uniform(self.jmin, self.jmax))
                try:
                    r = await self._requisitar(url, params, headers)
                except Exception as e:  # noqa: BLE001 - rede instável: tenta de novo
                    ultimo = e
                    await self._dormir(espera + random.random())
                    espera = min(espera * 2, 120)
                    continue
            if r.status in aceitar:
                return r
            if r.status in (429, 503, 502, 520, 403):
                ra = r.headers.get("retry-after") or r.headers.get("Retry-After")
                pausa = float(ra) if ra and ra.isdigit() else espera
                if self._ao_bloqueio:
                    self._ao_bloqueio(f"HTTP {r.status} em {url} (tentativa {tentativa}, pausa {pausa:.0f}s)")
                self.trocar_ua()
                await self._dormir(pausa + random.uniform(0, 1.5))
                espera = min(espera * 2, 180)
                if r.status == 403 and tentativa >= 3:
                    raise Bloqueado(f"403 persistente em {url}")
                continue
            if r.status == 404:
                raise FileNotFoundError(url)
            ultimo = RuntimeError(f"HTTP {r.status} em {url}")
            await self._dormir(espera)
            espera = min(espera * 2, 120)
        if isinstance(ultimo, Exception):
            raise ultimo
        raise Bloqueado(f"desisti de {url} após {self.tentativas} tentativas")

    async def _requisitar(self, url: str, params: dict | None, headers: dict | None) -> Resposta:
        h = {"User-Agent": self._ua, **self.headers, **(headers or {})}
        if self._curl is not None:
            if "User-Agent" in h and not (headers or {}).get("User-Agent"):
                h.pop("User-Agent")  # curl_cffi usa o UA coerente com a impressão TLS
            r = await self._curl.get(url, params=params, headers=h, allow_redirects=True)
            return Resposta(r.status_code, {k.lower(): v for k, v in r.headers.items()}, r.text, r.content, str(r.url))
        r = await self._httpx.get(url, params=params, headers=h)
        return Resposta(r.status_code, {k.lower(): v for k, v in r.headers.items()}, r.text, r.content, str(r.url))
