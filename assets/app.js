// Tema claro/escuro (lembra a escolha no navegador) e utilidades de formatação
(function () {
  const KEY = "tema";
  try { const t = localStorage.getItem(KEY); if (t) document.documentElement.dataset.theme = t; } catch (e) {}
  window.alternarTema = function () {
    const atual = document.documentElement.dataset.theme ||
      (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const novo = atual === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = novo;
    try { localStorage.setItem(KEY, novo); } catch (e) {}
    window.dispatchEvent(new Event("temamudou"));
  };
  window.css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  window.fmt = (v, d = 0) => v == null ? "–" : Number(v).toLocaleString("pt-BR", { minimumFractionDigits: d, maximumFractionDigits: d });
  window.brl = (v, d = 2) => v == null ? "–" : "R$ " + fmt(v, d);
})();
