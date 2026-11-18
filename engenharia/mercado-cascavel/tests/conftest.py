import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest

from app import config as cfgmod
from app.config import Config


@pytest.fixture()
def cfg(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MERCADO_DATA", str(tmp_path))
    monkeypatch.setenv("JITTER_MIN", "0")
    monkeypatch.setenv("JITTER_MAX", "0")
    monkeypatch.setenv("AGENDADOR", "0")
    monkeypatch.setenv("PAINEL_SENHA", "")
    from cryptography.fernet import Fernet
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    c = Config()
    cfgmod.redefinir(c)
    c.garantir_pastas()
    yield c
    cfgmod.redefinir(None)  # type: ignore[arg-type]


@pytest.fixture()
def con(cfg):
    from app.database import conectar, inicializar
    c = inicializar(conectar())
    yield c
    c.close()


async def _sem_espera(_s):
    return None


def cliente_mock(rotas):
    """rotas: função (request) -> httpx.Response"""
    from app.http import ClienteEducado
    return ClienteEducado(transporte=httpx.MockTransport(rotas), dormir=_sem_espera)


PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 300)


def vtex_produto(pid, sku, nome, marca, ean, preco, lista=None, disp=True, img="https://img.x/1.jpg", seller="1"):
    return {
        "productId": pid, "productName": nome, "brand": marca, "link": f"https://loja/{pid}/p",
        "categories": ["/Mercearia/Café/"], "linkText": str(pid),
        "items": [{"itemId": sku, "nameComplete": nome, "ean": ean, "images": [{"imageUrl": img}],
                   "sellers": [{"sellerId": seller, "sellerDefault": True, "commertialOffer": {
                       "Price": preco, "ListPrice": lista or preco, "IsAvailable": disp,
                       "AvailableQuantity": 10 if disp else 0}}]}],
    }
