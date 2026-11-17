"""CLI: python -m app <comando>

  servir                 sobe o painel (uvicorn) na porta $PORTA (8100)
  coletar [slug|todas]   roda a coleta agora, no terminal
  sondar <slug> [termo]  teste rápido de um coletor: busca um termo e mostra 5 itens
  gerar-chave            gera uma FERNET_KEY nova para o .env
"""
from __future__ import annotations

import asyncio
import json
import os
import sys


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "servir"
    if cmd == "gerar-chave":
        from .cofre import gerar_chave
        print(gerar_chave())
        return 0
    if cmd == "servir":
        import uvicorn
        uvicorn.run("app.main:app", host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORTA", "8100")),
                    proxy_headers=True, log_level="info", workers=1)
        return 0

    from .config import config
    from .database import conectar, inicializar, parametros
    from .eventos import barramento
    config().garantir_pastas()
    con = inicializar(conectar())
    barramento.ligar(con)

    if cmd == "coletar":
        from . import coleta
        alvo = argv[1] if len(argv) > 1 else "todas"
        res = asyncio.run(coleta.coletar_todas(con) if alvo == "todas" else coleta.coletar_loja(con, alvo))
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    if cmd == "sondar":
        from .http import ClienteEducado
        from .scrapers import fabricar
        from .scrapers.base import Contexto
        from .scrapers.marketplaces import BUSCADORES, pontuar
        slug, termo = argv[1], (argv[2] if len(argv) > 2 else "cafe")

        async def rodar() -> None:
            async with ClienteEducado(ao_bloqueio=print) as cli:
                if slug in BUSCADORES:
                    for o in pontuar(termo, await BUSCADORES[slug](cli, termo))[:5]:
                        print(f"{o.preco:>9.2f}  {o.score:5.1f}%  {o.titulo[:70]}  {o.url}")
                    return
                loja = con.execute("SELECT * FROM lojas WHERE slug=?", (slug,)).fetchone()
                col = fabricar(loja, parametros(loja), cli, Contexto(log=lambda n, m: print(n, m)))
                if hasattr(col, "buscar"):
                    itens = await col.buscar(termo, 5)
                else:
                    itens = []
                    async for it in col.coletar():
                        itens.append(it)
                        if len(itens) >= 5:
                            break
                for it in itens[:5]:
                    print(f"{it.preco:>9.2f}  promo={it.preco_promocional}  ean={it.ean}  sku={it.sku}  {it.nome[:60]}")
                print(f"OK: {len(itens)} itens" if itens else "NADA ENCONTRADO")

        asyncio.run(rodar())
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
