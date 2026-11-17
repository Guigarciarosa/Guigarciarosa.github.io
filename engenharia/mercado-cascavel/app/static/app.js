// Mercado Cascavel: comportamento do painel (Alpine.js + EventSource). Sem build, sem CDN.
const brl = (v) => (v === null || v === undefined) ? "—" :
  Number(v).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
window.brl = brl;

function postForm(url, dados = {}) {
  const fd = new FormData();
  Object.entries(dados).forEach(([k, v]) => v !== undefined && v !== null && fd.append(k, v));
  return fetch(url, { method: "POST", body: fd }).then((r) => r.ok ? r.json().catch(() => ({})) : Promise.reject(r));
}

window.carrinhoApp = () => ({
  modo: "dividido", marketplaces: false, plano: null, sessoes: [], abrir: [], brl,
  async carregar() {
    this.plano = await fetch(`/api/carrinho?marketplaces=${this.marketplaces}`).then((r) => r.json());
    if (this.plano.dividido && this.plano.dividido.grupos.length === 1) this.modo = "unica";
    this.sessoes = await fetch("/api/checkout").then((r) => r.json());
  },
  async qtd(id, q) {
    await postForm(`/carrinho/${id}`, { quantidade: q, somar: false });
    window.dispatchEvent(new CustomEvent("carrinho", { detail: { value: null } }));
    this.carregar();
  },
  async limpar() { await fetch("/carrinho", { method: "DELETE" }); location.reload(); },
  async links(modo, slug) {
    const q = new URLSearchParams({ modo, marketplaces: this.marketplaces });
    if (slug) q.set("slug", slug);
    this.abrir = await fetch(`/api/checkout/links?${q}`).then((r) => r.json());
  },
  async checkout(modo, slug) {
    await postForm("/api/checkout", { modo, slug, marketplaces: this.marketplaces });
    const es = new EventSource("/api/eventos");
    es.addEventListener("checkout", (e) => {
      const s = JSON.parse(e.data);
      const i = this.sessoes.findIndex((x) => x.slug === s.slug);
      i >= 0 ? this.sessoes.splice(i, 1, s) : this.sessoes.push(s);
    });
  },
});

window.painelColeta = (progresso, logs) => ({
  prog: progresso || {}, logs: (logs || []), nivel: "", busca: "", seguir: true, online: false,
  p(slug) { return this.prog[slug] || {}; },
  soma(campo) { return Object.values(this.prog).reduce((a, p) => a + (p[campo] || 0), 0).toLocaleString("pt-BR"); },
  cor(s) { return { rodando: "text-sky-300", ok: "text-emerald-300", erro: "text-red-300", parcial: "text-amber-300" }[s] || "text-zinc-500"; },
  linha(p, nome) {
    if (!p.total && !p.lidos) return `${nome}: aguardando`;
    const t = p.total ? p.total.toLocaleString("pt-BR") : "?";
    return `${nome}: ${(p.lidos || 0).toLocaleString("pt-BR")}/${p.total_estimado ? "≈" : ""}${t} - ${p.pct || 0}%`;
  },
  filtrados() {
    const b = this.busca.toLowerCase();
    const ordem = { INFO: 0, WARNING: 1, ERROR: 2 };
    return this.logs.filter((l) => (!this.nivel || ordem[l.nivel] >= ordem[this.nivel]) &&
      (!b || (l.mensagem + " " + l.origem).toLowerCase().includes(b))).slice(0, 600).reverse();
  },
  conectar() {
    const es = new EventSource("/api/eventos");
    es.onopen = () => (this.online = true);
    es.onerror = () => (this.online = false);
    es.addEventListener("progresso", (e) => { const d = JSON.parse(e.data); this.prog = { ...this.prog, [d.slug]: d }; });
    es.addEventListener("log", (e) => {
      this.logs.unshift(JSON.parse(e.data));
      if (this.logs.length > 2000) this.logs.length = 2000;
      if (this.seguir) this.$nextTick(() => { const c = this.$refs.caixa; if (c) c.scrollTop = c.scrollHeight; });
    });
  },
  sync(slug) { postForm(slug ? `/api/sync/${slug}` : "/api/sync"); },
});

// Gráfico de histórico em SVG puro (sem biblioteca: pouca CPU/RAM no servidor e no cliente)
window.desenharHistorico = (el) => {
  if (!el) return;
  const series = JSON.parse(el.dataset.series || "{}");
  const nomes = Object.keys(series);
  const pts = nomes.flatMap((n) => series[n]);
  if (pts.length < 2) { el.innerHTML = '<p class="p-6 text-center text-sm text-zinc-500">O histórico aparece depois de algumas coletas.</p>'; return; }
  const cores = ["#34d399", "#60a5fa", "#f472b6", "#fbbf24", "#a78bfa", "#f87171", "#2dd4bf", "#fb923c"];
  const W = el.clientWidth - 24, H = el.clientHeight - 24, m = { l: 52, r: 8, t: 8, b: 22 };
  const datas = [...new Set(pts.map((p) => p[0]))].sort();
  const t = (d) => new Date(d).getTime();
  const x0 = t(datas[0]), x1 = Math.max(t(datas[datas.length - 1]), x0 + 864e5);
  const ys = pts.map((p) => p[1]); const y0 = Math.min(...ys) * 0.97, y1 = Math.max(...ys) * 1.03;
  const X = (d) => m.l + (t(d) - x0) / (x1 - x0) * (W - m.l - m.r);
  const Y = (v) => H - m.b - (v - y0) / (y1 - y0 || 1) * (H - m.t - m.b);
  let svg = `<svg width="${W}" height="${H}" class="overflow-visible">`;
  for (let i = 0; i <= 3; i++) { const v = y0 + (y1 - y0) * i / 3; svg += `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="#ffffff12"/><text x="${m.l - 6}" y="${Y(v) + 4}" fill="#71717a" font-size="10" text-anchor="end">${brl(v)}</text>`; }
  svg += `<text x="${m.l}" y="${H - 4}" fill="#71717a" font-size="10">${datas[0]}</text><text x="${W - m.r}" y="${H - 4}" fill="#71717a" font-size="10" text-anchor="end">${datas[datas.length - 1]}</text>`;
  nomes.forEach((n, i) => {
    const s = series[n]; const c = cores[i % cores.length];
    let d = ""; s.forEach((p, j) => { d += (j ? ` H${X(p[0])} V${Y(p[1])}` : `M${X(p[0])} ${Y(p[1])}`); });
    d += ` H${W - m.r}`;
    svg += `<path d="${d}" fill="none" stroke="${c}" stroke-width="2"/>`;
  });
  svg += "</svg>";
  const leg = nomes.map((n, i) => `<span class="mr-3 inline-flex items-center gap-1 text-[11px] text-zinc-400"><i style="background:${cores[i % cores.length]}" class="inline-block h-2 w-2 rounded-full"></i>${n}</span>`).join("");
  el.innerHTML = svg + `<div class="mt-1">${leg}</div>`;
};
