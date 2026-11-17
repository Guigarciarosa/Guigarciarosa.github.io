# syntax=docker/dockerfile:1.7
# ---------- estágio 1: compila as wheels (gcc fica só aqui) ----------
FROM python:3.11-slim AS construtor
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /w
COPY requirements.txt .
# (o secret "ca" é opcional: só serve para builds atrás de proxy com CA própria)
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip wheel --wheel-dir /wheels -r requirements.txt

# ---------- estágio 2: imagem final enxuta ----------
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright MERCADO_DATA=/data TZ=America/Sao_Paulo DISPLAY=:99
# Xvfb + x11vnc + noVNC: só para você ver/assumir as abas do checkout pelo navegador (porta 6080)
RUN --mount=type=secret,id=ca,required=false \
    sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources; \
    CA=""; if [ -f /run/secrets/ca ]; then CA="-o Acquire::https::CAInfo=/run/secrets/ca"; fi; \
    apt-get $CA update && apt-get $CA install -y --no-install-recommends xvfb x11vnc novnc websockify tzdata libatomic1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=construtor /wheels /wheels
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/ca PIP_CERT=/run/secrets/ca; \
      printf 'Acquire::https::CAInfo "/run/secrets/ca";\n' > /etc/apt/apt.conf.d/99ca; fi; \
    pip install /wheels/* && rm -rf /wheels \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* /root/.cache /etc/apt/apt.conf.d/99ca
RUN useradd --uid 1000 --create-home mercado && mkdir -p /data && chown mercado /data
WORKDIR /app
COPY app ./app
COPY docker/entrada.sh /entrada.sh
USER mercado
EXPOSE 8100 6080
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORTA','8100')+'/saude',timeout=4)"
ENTRYPOINT ["sh", "/entrada.sh"]
