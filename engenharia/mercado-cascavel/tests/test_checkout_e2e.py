"""Checkout real com Chromium contra uma loja falsa local. Pulado se o Playwright/Chromium não estiver
instalado (roda dentro da imagem Docker e em dev com `playwright install chromium`)."""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pw = pytest.importorskip("playwright.async_api")

POSTS: list[str] = []


class LojaFalsa(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/checkout/cart/add"):
            self.send_response(302)
            self.send_header("Location", "/checkout/#/cart")
            self.end_headers()
            return
        corpo = b"<html><body><h1>Loja</h1>"
        if self.path.startswith("/checkout"):
            # página hostil: tenta fechar o pedido sozinha enquanto o robô está no controle
            corpo += (b"<button>Finalizar pedido</button><script>"
                      b"fetch('/api/checkout/pub/orderForm/abc/transaction',{method:'POST'})</script>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(corpo + b"</body></html>")

    def do_POST(self):
        POSTS.append(self.path)
        self.send_response(200)
        self.end_headers()


@pytest.mark.asyncio
async def test_checkout_para_na_revisao_e_bloqueia_pagamento(con, cfg, monkeypatch):
    monkeypatch.setattr(cfg, "checkout_headless", True)
    srv = HTTPServer(("127.0.0.1", 0), LojaFalsa)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    con.execute("UPDATE lojas SET url_base=? WHERE slug='muffato'", (base,))
    from app.checkout.worker import GerenteCheckout
    g = GerenteCheckout()
    try:
        est = await g.iniciar(con, [{"slug": "muffato", "itens": [{"sku": "100", "quantidade": 2, "seller": "1"}]}])
    except Exception as e:  # Chromium ausente no ambiente
        pytest.skip(f"Chromium indisponível: {e}")
    finally:
        await g.fechar()
        if g._pw:
            await g._pw.stop()
        srv.shutdown()
    s = est[0]
    if s["status"] == "erro" and "Executable doesn't exist" in s["mensagem"]:
        pytest.skip("Chromium do Playwright não instalado")
    assert s["status"] == "pronto" and s["url_final"].endswith("/checkout/#/cart")
    assert any("bloqueado pelo robô" in a for a in s["alertas"])
    assert POSTS == []   # nenhum POST de pagamento chegou à loja
