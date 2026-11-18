#!/bin/sh
# Sobe a tela virtual (para o checkout com navegador visível) e depois o painel.
set -e
if [ "${CHECKOUT_VNC:-1}" = "1" ]; then
  Xvfb :99 -screen 0 1360x900x24 -nolisten tcp >/dev/null 2>&1 &
  sleep 1
  if [ -n "${VNC_SENHA:-}" ]; then
    mkdir -p "$HOME/.vnc" && x11vnc -storepasswd "$VNC_SENHA" "$HOME/.vnc/senha" >/dev/null 2>&1
    x11vnc -display :99 -rfbauth "$HOME/.vnc/senha" -localhost -forever -shared -quiet -bg -o /dev/null
  else
    echo "AVISO: VNC_SENHA vazia, noVNC desligado" >&2
  fi
  [ -n "${VNC_SENHA:-}" ] && websockify --web /usr/share/novnc 6080 localhost:5900 >/dev/null 2>&1 &
fi
exec python -m app servir
