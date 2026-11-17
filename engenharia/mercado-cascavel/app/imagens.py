"""Imagens de produto: baixadas UMA vez, quando o produto canônico nasce. Um arquivo por id_produto."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import config
from .database import transacao
from .http import ClienteEducado

_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif", "image/avif": ".avif"}


def caminho_relativo(id_produto: int, ext: str) -> str:
    # 2 níveis de pasta para não juntar 50 mil arquivos num diretório só
    return f"{id_produto % 100:02d}/{id_produto}{ext}"


async def baixar(con: sqlite3.Connection, cliente: ClienteEducado, id_produto: int, url: str) -> str | None:
    atual = con.execute("SELECT imagem_local_path FROM produtos_mestres WHERE id_produto=?", (id_produto,)).fetchone()
    if not atual or atual["imagem_local_path"]:
        return atual["imagem_local_path"] if atual else None  # já existe: nunca baixa de novo
    if url.startswith("//"):
        url = "https:" + url
    try:
        r = await cliente.get(url, headers={"Accept": "image/avif,image/webp,image/*"}, jitter=False)
    except Exception:  # noqa: BLE001
        return None
    ct = r.headers.get("content-type", "").split(";")[0].strip()
    ext = _EXT.get(ct) or Path(url.split("?")[0]).suffix.lower() or ".jpg"
    if len(r.conteudo) < 200 or len(r.conteudo) > 5_000_000:
        return None
    rel = caminho_relativo(id_produto, ext)
    destino = config().media_dir / rel
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(r.conteudo)
    with transacao(con):
        con.execute("UPDATE produtos_mestres SET imagem_local_path=? WHERE id_produto=? AND imagem_local_path IS NULL",
                    (rel, id_produto))
    return rel
