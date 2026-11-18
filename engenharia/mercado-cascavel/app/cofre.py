"""Cofre de credenciais: usuário, senha e cookies das lojas cifrados com Fernet (chave só no .env)."""
from __future__ import annotations

import json
import sqlite3

from cryptography.fernet import Fernet, InvalidToken

from .config import config
from .database import agora, transacao


class CofreIndisponivel(RuntimeError):
    pass


def _fernet() -> Fernet:
    chave = config().fernet_key
    if not chave:
        raise CofreIndisponivel("FERNET_KEY não definida no .env")
    try:
        return Fernet(chave.encode())
    except (ValueError, TypeError) as e:
        raise CofreIndisponivel("FERNET_KEY inválida (gere com: python -m app gerar-chave)") from e


def gerar_chave() -> str:
    return Fernet.generate_key().decode()


def _cifrar(txt: str | None) -> str | None:
    return None if txt in (None, "") else _fernet().encrypt(txt.encode()).decode()


def _decifrar(txt: str | None) -> str | None:
    if not txt:
        return None
    try:
        return _fernet().decrypt(txt.encode()).decode()
    except InvalidToken as e:
        raise CofreIndisponivel("não foi possível decifrar (FERNET_KEY trocada?)") from e


def salvar(con: sqlite3.Connection, id_loja: int, usuario: str | None = None, senha: str | None = None,
           cookies: list[dict] | None = None) -> None:
    atual = con.execute("SELECT * FROM credenciais_lojas WHERE id_loja=?", (id_loja,)).fetchone()
    u = _cifrar(usuario) if usuario is not None else (atual["usuario_cifrado"] if atual else None)
    s = _cifrar(senha) if senha is not None else (atual["senha_cifrada"] if atual else None)
    c = _cifrar(json.dumps(cookies)) if cookies is not None else (atual["cookies_cifrados"] if atual else None)
    with transacao(con):
        con.execute(
            """INSERT INTO credenciais_lojas (id_loja, usuario_cifrado, senha_cifrada, cookies_cifrados, atualizado_em)
               VALUES (?,?,?,?,?) ON CONFLICT(id_loja) DO UPDATE SET usuario_cifrado=excluded.usuario_cifrado,
               senha_cifrada=excluded.senha_cifrada, cookies_cifrados=excluded.cookies_cifrados,
               atualizado_em=excluded.atualizado_em""",
            (id_loja, u, s, c, agora()),
        )


def ler(con: sqlite3.Connection, id_loja: int) -> dict | None:
    r = con.execute("SELECT * FROM credenciais_lojas WHERE id_loja=?", (id_loja,)).fetchone()
    if not r:
        return None
    cookies = _decifrar(r["cookies_cifrados"])
    return {
        "usuario": _decifrar(r["usuario_cifrado"]),
        "senha": _decifrar(r["senha_cifrada"]),
        "cookies": json.loads(cookies) if cookies else [],
    }


def resumo(con: sqlite3.Connection) -> dict[int, dict]:
    """O que o painel pode mostrar: só se existe, nunca o valor."""
    out = {}
    for r in con.execute("SELECT * FROM credenciais_lojas"):
        out[r["id_loja"]] = {
            "tem_usuario": bool(r["usuario_cifrado"]),
            "tem_senha": bool(r["senha_cifrada"]),
            "tem_cookies": bool(r["cookies_cifrados"]),
            "atualizado_em": r["atualizado_em"],
        }
    return out


def apagar(con: sqlite3.Connection, id_loja: int) -> None:
    with transacao(con):
        con.execute("DELETE FROM credenciais_lojas WHERE id_loja=?", (id_loja,))
