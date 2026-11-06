// Dashboard do Projeto 1: lê data/poder_compra.json (gold) e data/contexto.json (linha do tempo)
const M = {
  bigmacs:         { nome: "Big Macs",            curto: "Big Mac",    css: "--series-1", un: "",    d: 1, desde: "1995", fonte: "The Economist", preco: "bigmac_preco", precoUn: "R$" },
  litros_gasolina: { nome: "Litros de gasolina",  curto: "Gasolina",   css: "--series-2", un: " l",  d: 0, desde: "2001", fonte: "ANP",           preco: "gasolina_litro", precoUn: "R$/l" },
  m2_aluguel_sp:   { nome: "m² de aluguel em SP", curto: "Aluguel SP", css: "--series-3", un: " m²", d: 1, desde: "2008", fonte: "FipeZAP",       preco: "aluguel_m2_sp", precoUn: "R$/m²" },
};
let D, CTX = { eventos: [] }, filtro = "todos", charts = {};
const $ = (id) => document.getElementById(id);
const ativos = () => filtro === "todos" ? Object.keys(M) : [filtro];
const porAno = (a) => D.anual.find(r => Number(r.ano) === Number(a)) || {};

Promise.all([
  fetch("../../data/poder_compra.json").then(r => r.json()),
  fetch("../../data/contexto.json").then(r => r.ok ? r.json() : { eventos: [] }).catch(() => ({ eventos: [] })),
]).then(([d, c]) => {
  D = d; CTX = c;
  D.anual.forEach(r => r.ano = Number(r.ano));   // o JSON pode vir com ano como texto (Databricks)
  D.mensal = D.mensal || [];
  montar();
});

function montar() {
  $("ano-atual").textContent = new Date().getFullYear();
  const dt = new Date(D.gerado_em).toLocaleDateString("pt-BR");
  $("motor").textContent = `Dados de ${dt} · processados no ${D.motor === "databricks" ? "Databricks" : D.motor === "snowflake" ? "Snowflake" : "DuckDB"}`;

  const anos = D.anual.map(r => r.ano), ult = anos.at(-1);
  for (const id of ["anoA", "anoB"]) $(id).innerHTML = anos.map(a => `<option value="${a}">${a}</option>`).join("");
  $("anoA").value = anos[0]; $("anoB").value = ult;
  $("anoA").onchange = $("anoB").onchange = kpis;

  const comTudo = D.anual.filter(r => r.bigmacs && r.litros_gasolina && r.m2_aluguel_sp).map(r => r.ano);
  const bases = comTudo.length ? comTudo : D.anual.filter(r => r.bigmacs && r.litros_gasolina).map(r => r.ano);
  $("base").innerHTML = bases.map(a => `<option value="${a}">${a}</option>`).join("");
  $("base").onchange = desenhar;

  const anosAnp = [...new Set(D.mensal.filter(m => m.gasolina_litro).map(m => m.mes.slice(0, 4)))];
  $("anoAnp").innerHTML = `<option value="">Todos</option>` + anosAnp.reverse().map(a => `<option>${a}</option>`).join("");
  $("anoAnp").onchange = tabelaAnp;

  document.querySelectorAll("#filtro button").forEach(b => b.onclick = () => {
    filtro = b.dataset.f;
    document.querySelectorAll("#filtro button").forEach(x => x.setAttribute("aria-pressed", x === b));
    aplicarFiltro();
  });

  fatos(); bastidores(); aplicarFiltro();
  window.addEventListener("temamudou", desenhar);
  window.addEventListener("resize", () => Object.values(charts).forEach(c => c.resize()));
}

function aplicarFiltro() {
  // mostra só os cartões/seções da métrica escolhida (salário e índice sempre aparecem)
  document.querySelectorAll("[data-m]").forEach(el => {
    const m = el.dataset.m;
    el.style.display = (m === "salario" || m === "indice" || filtro === "todos" || m === filtro) ? "" : "none";
  });
  if (filtro !== "todos") {             // comparador começa no 1º ano com dado da métrica
    const primeiro = D.anual.find(r => r[filtro] != null);
    const ultimo = [...D.anual].reverse().find(r => r[filtro] != null);
    if (primeiro && porAno($("anoA").value)[filtro] == null) $("anoA").value = primeiro.ano;
    if (ultimo && porAno($("anoB").value)[filtro] == null) $("anoB").value = ultimo.ano;
  }
  historia(); kpis(); tabela(); tabelaAnp(); timeline(); desenhar();
}

// ---------------------------------------------------------------- textos
function historia() {
  const k = filtro === "todos" ? "bigmacs" : filtro, m = M[k];
  const com = D.anual.filter(r => r[k] != null);
  $("story-eyebrow").textContent = `${m.nome} por salário mínimo`;
  $("story-eyebrow").style.color = `var(${m.css})`;
  if (!com.length) { $("story-num").textContent = "–"; $("story-txt").textContent = "Sem dados desta fonte ainda."; return; }
  const a = com[0], b = com.at(-1);
  const va = k === "bigmacs" && a.ano === 1995 ? a.bigmacs_maior_salario_ano : a[k];
  $("story-num").textContent = `${fmt(va, m.d)} → ${fmt(b[k], m.d)}`;
  const txt = {
    bigmacs: `Em maio de 1995 o mínimo foi a R$ 100 e o Big Mac custava R$ 2,45: <b>${fmt(va, 1)} sanduíches</b>. Em ${b.ano}, com o mínimo a ${brl(b.salario_medio, 0)} e o Big Mac a ${brl(b.bigmac_preco)}, são <b>${fmt(b[k], 1)}</b>.`,
    litros_gasolina: `Em ${a.ano}, com o litro a ${brl(a.gasolina_litro, 3)}, o mínimo enchia <b>${fmt(va, 0)} litros</b>. Em ${b.ano}, a ${brl(b.gasolina_litro, 3)}, enche <b>${fmt(b[k], 0)}</b>.`,
    m2_aluguel_sp: `Em ${a.ano} o m² de aluguel em São Paulo custava ${brl(a.aluguel_m2_sp)} e o mínimo pagava <b>${fmt(va, 1)} m²</b>. Em ${b.ano}, a ${brl(b.aluguel_m2_sp)}/m², paga <b>${fmt(b[k], 1)} m²</b>.`,
  }[k];
  $("story-txt").innerHTML = txt;
}

function fatos() {
  const r95 = porAno(1995), r04 = porAno(2004), r09 = porAno(2009);
  const card = (cor, titulo, valor, texto, status) => `
    <div class="card kpi">
      <div class="label"><span class="dot" style="background:var(${cor})"></span>${titulo}</div>
      <div class="value">${valor}</div>
      <div class="delta">${texto}</div>
      <div class="small" style="margin-top:8px"><span class="badge">${status}</span></div>
    </div>`;
  const al = r09.aluguel_m2_sp;
  $("fatos").innerHTML =
    card("--series-1", "1995 · Big Macs com um mínimo", fmt(r95.bigmacs_maior_salario_ano, 1),
      `Confere: a partir de maio (mínimo R$ 100, Big Mac R$ 2,45). Até abril, com R$ 70, eram ${fmt(70 / r95.bigmac_preco, 1)}. Na média do ano, ${fmt(r95.bigmacs, 1)}.`,
      "✓ The Economist + BCB") +
    card("--series-3", "2009 · aluguel em São Paulo", al ? brl(al) + "/m²" : "sem dado",
      al ? `Média de 2009. Um apartamento de 50 m² saía por ${brl(r09.aluguel_50m2_sp, 0)}/mês, ${fmt(r09.pct_salario_aluguel_50m2, 0)}% do mínimo médio (${brl(r09.salario_medio, 0)}). O mínimo pagava ${fmt(r09.m2_aluguel_sp, 1)} m².`
         : "O FipeZAP mede preço por m², desde jan/2008. O valor aparece depois que o pipeline baixa a planilha da Fipe.",
      al ? "✓ FipeZAP Locação" : "⏳ FipeZAP Locação") +
    card("--series-2", "2004 · litro da gasolina", r04.gasolina_litro ? brl(r04.gasolina_litro, 3) : "R$ 2,137",
      `Média anual da ANP. Com o mínimo médio de ${brl(r04.salario_medio, 0)}, dava ${fmt(r04.litros_gasolina || r04.salario_medio / 2.137, 0)} litros.`,
      r04.gasolina_litro ? "✓ ANP" : "✓ ANP (valor conferido)");
}

function kpis() {
  const a = +$("anoA").value, b = +$("anoB").value, ra = porAno(a), rb = porAno(b);
  const item = (cor, nome, k, d, un = "", pre = "") => {
    const va = ra[k], vb = rb[k], pct = va && vb ? (vb / va - 1) * 100 : null;
    const v = (x) => x == null ? `<span class="muted" title="sem dado da fonte neste ano">sem dado</span>` : pre + fmt(x, d) + un;
    return `<div class="card kpi">
      <div class="label"><span class="dot" style="background:var(${cor})"></span>${nome}</div>
      <div class="value">${v(vb)}</div>
      <div class="delta">${a}: ${v(va)}${pct == null ? "" : ` · <b>${pct >= 0 ? "+" : ""}${fmt(pct, 0)}%</b>`}</div>
    </div>`;
  };
  $("kpis").innerHTML = ativos().map(k => item(M[k].css, M[k].nome, k, M[k].d, M[k].un)).join("") +
    item("--series-4", "Salário real (R$ de hoje)", "salario_real_hoje", 0, "", "R$ ");
  const falta = ativos().filter(k => ra[k] == null || rb[k] == null);
  $("cobertura").textContent = "Salário do ano = média dos 12 meses (em vários anos houve dois valores). " +
    (falta.length ? `Cobertura das fontes: ${falta.map(k => `${M[k].curto} desde ${M[k].desde}`).join(", ")}.` : "");
}

// ---------------------------------------------------------------- tabelas
function tabela() {
  const base = [["ano", "Ano", v => v], ["salario_medio", "Mínimo (R$)", v => fmt(v, 2)], ["salario_real_hoje", "Mínimo real", v => fmt(v, 0)], ["ipca_pct", "IPCA %", v => fmt(v, 2)]];
  const por = {
    bigmacs: [["bigmac_preco", "Big Mac (R$)", v => fmt(v, 2)], ["bigmacs", "Big Macs", v => fmt(v, 1)]],
    litros_gasolina: [["gasolina_litro", "Gasolina (R$/l)", v => fmt(v, 3)], ["litros_gasolina", "Litros", v => fmt(v, 0)]],
    m2_aluguel_sp: [["aluguel_m2_sp", "Aluguel SP (R$/m²)", v => fmt(v, 2)], ["m2_aluguel_sp", "m²", v => fmt(v, 1)], ["pct_salario_aluguel_50m2", "50 m² (% do mínimo)", v => fmt(v, 0) + "%"]],
  };
  const cols = base.concat(...ativos().map(k => por[k]));
  $("tabela").innerHTML = `<thead><tr>${cols.map(c => `<th>${c[1]}</th>`).join("")}</tr></thead><tbody>` +
    [...D.anual].reverse().map(r => `<tr>${cols.map(c => `<td>${r[c[0]] == null ? "–" : c[2](r[c[0]])}</td>`).join("")}</tr>`).join("") + "</tbody>";
}

function tabelaAnp() {
  const ano = $("anoAnp").value;
  const linhas = D.mensal.filter(m => m.gasolina_litro && (!ano || m.mes.startsWith(ano))).reverse();
  const mesTxt = (m) => new Date(m + "-15").toLocaleDateString("pt-BR", { month: "short", year: "numeric" });
  $("tab-anp").innerHTML = "<thead><tr><th>Mês</th><th>Gasolina comum (R$/l)</th><th>Salário mínimo (R$)</th><th>Litros por salário</th></tr></thead><tbody>" +
    (linhas.length ? linhas.map(m => `<tr><td>${mesTxt(m.mes)}</td><td>${fmt(m.gasolina_litro, 3)}</td><td>${fmt(m.salario_minimo, 2)}</td><td>${fmt(m.litros_gasolina, 0)}</td></tr>`).join("")
      : `<tr><td colspan="4" class="muted">A série mensal da ANP entra quando o pipeline baixar as planilhas oficiais.</td></tr>`) + "</tbody>";
}

function timeline() {
  const ev = CTX.eventos.filter(e => filtro === "todos" || e.metrica === filtro || e.metrica === "salario_real_hoje");
  $("timeline").innerHTML = ev.map(e => {
    const cor = M[e.metrica]?.css || "--series-4";
    return `<article class="tl-item">
      <div class="tl-ano" style="color:var(${cor})">${e.ano}</div>
      <div class="card">
        <h3>${e.titulo}</h3>
        <p class="small muted" style="margin:6px 0 10px">${e.texto}</p>
        <ul class="tl-fontes">${e.fontes.map(f => `<li><a href="${f.url}" target="_blank" rel="noopener">${f.nome} ↗</a></li>`).join("")}</ul>
      </div>
    </article>`;
  }).join("");
}

function bastidores() {
  const orig = { online: "✓ online", snapshot: "snapshot", seed: "valor conferido" };
  $("fontes").innerHTML = "<thead><tr><th>Tabela</th><th>Linhas</th><th>Origem</th></tr></thead><tbody>" +
    D.fontes.map(f => `<tr><td>${f.url && f.url.startsWith("http") ? `<a href="${f.url}" target="_blank" rel="noopener">${f.tabela}</a>` : f.tabela}</td><td>${fmt(f.linhas ?? f.linhas_novas)}</td><td>${orig[f.origem] || f.origem}</td></tr>`).join("") + "</tbody>";
  $("testes").innerHTML = D.qualidade.map(q =>
    `<div style="display:flex;gap:10px;padding:6px 0;border-bottom:1px solid var(--border)"><span>${q.ok ? "✓" : "✗"}</span><span class="small">${q.teste}</span></div>`).join("");
  fetch("../../assets/prints/prints.json").then(r => r.ok ? r.json() : []).catch(() => []).then(lista => {
    const fig = lista.filter(p => p.projeto === "poder-de-compra").map(p =>
      `<figure><img src="../../assets/prints/${p.arquivo}" alt="${p.legenda}" loading="lazy" onclick="zoom(this)"><figcaption><b>${p.ferramenta}</b> · ${p.legenda}</figcaption></figure>`);
    const faltam = ["Catálogo: schema poder_compra com bronze, silver e gold", "Volume bronze_raw com os arquivos enviados", "SQL Editor rodando a gold", "Linhagem (lineage) da gold_poder_compra_anual", "Dashboard nativo do Databricks (AI/BI)"]
      .slice(fig.length).map(t => `<div class="placeholder">📸 ${t}</div>`);
    $("galeria").innerHTML = fig.concat(faltam).join("");
  });
}
function zoom(img) { const lb = $("lb"); lb.querySelector("img").src = img.src; lb.classList.add("on"); }

// ---------------------------------------------------------------- gráficos
function grafico(el, opt) {
  charts[el]?.dispose();
  const node = $(el);
  if (!node || node.offsetParent === null) return;   // escondido pelo filtro
  const c = echarts.init(node, null, { renderer: "svg" });
  charts[el] = c;
  c.setOption(Object.assign({
    animationDuration: 500,
    textStyle: { fontFamily: "Inter, system-ui", color: css("--text-secondary") },
    grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
  }, opt));
}
const eixoX = (dados, cada = 5) => ({ type: "category", data: dados, boundaryGap: false, axisLine: { lineStyle: { color: css("--grid") } }, axisTick: { show: false },
  axisLabel: { color: css("--text-secondary"), interval: (i, v) => cada ? Number(String(v).slice(0, 4)) % cada === 0 && (String(v).length === 4 || String(v).endsWith("-01")) : false } });
const eixoY = (f, extra = {}) => Object.assign({ type: "value", splitLine: { lineStyle: { color: css("--grid") } }, axisLabel: { color: css("--text-secondary"), formatter: f } }, extra);
const leg = () => ({ top: 0, left: 0, icon: "roundRect", itemWidth: 14, itemHeight: 4, textStyle: { color: css("--text-secondary") } });
const tip = (f) => ({ trigger: "axis", backgroundColor: css("--surface-1"), borderColor: css("--border"), textStyle: { color: css("--text-primary"), fontSize: 13 },
  axisPointer: { type: "line", lineStyle: { color: css("--text-muted") } },
  formatter: ps => `<b>${ps[0].axisValue}</b><br>` + ps.filter(p => p.seriesName).map(p => `${p.marker} ${p.seriesName}: ${p.value == null ? "sem dado" : f(p.value, p)}`).join("<br>") });
const area = (cor) => ({ areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: cor + "33" }, { offset: 1, color: cor + "00" }]) } });
const linha = (nome, cor, dados, extra = {}) => Object.assign({ name: nome, type: "line", data: dados, showSymbol: false, symbolSize: 8,
  lineStyle: { width: 2, color: cor }, itemStyle: { color: cor }, emphasis: { focus: "series" } }, extra);

function serieMetrica(el, k) {
  const m = M[k], cor = css(m.css), A = D.anual, anos = A.map(r => String(r.ano));
  const com = A.filter(r => r[k] != null);
  grafico(el, { xAxis: eixoX(anos), yAxis: eixoY(null, { scale: true }),
    series: [linha(m.nome, cor, A.map(r => r[k]), { ...area(cor), connectNulls: false,
      markPoint: com.length ? { symbol: "circle", symbolSize: 9, itemStyle: { color: cor, borderColor: css("--surface-1"), borderWidth: 2 },
        label: { show: true, position: "top", color: css("--text-primary"), fontWeight: 600, formatter: p => fmt(p.value, m.d) },
        data: [com[0], com.at(-1)].map(r => ({ coord: [String(r.ano), r[k]], value: r[k] })) } : undefined })],
    tooltip: tip((v, p) => `${fmt(v, m.d)}${m.un}${porAno(p.axisValue)[m.preco] != null ? ` <span style="opacity:.7">(${m.precoUn} ${fmt(porAno(p.axisValue)[m.preco], k === "litros_gasolina" ? 3 : 2)})</span>` : ""}`) });
}

function desenhar() {
  if (!D) return;
  const A = D.anual, anos = A.map(r => String(r.ano));
  serieMetrica("ch-story", filtro === "todos" ? "bigmacs" : filtro);

  const ultIpca = [...A].reverse().find(r => r.ipca_pct != null)?.ano;
  $("sub-real").textContent = `R$ por mês. Real = corrigido pelo IPCA, em R$ de dezembro de ${ultIpca}.`;
  grafico("ch-salario", { legend: leg(), grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
    xAxis: eixoX(anos), yAxis: eixoY(v => "R$ " + fmt(v)),
    series: [linha("Nominal", css("--text-muted"), A.map(r => r.salario_medio), { lineStyle: { width: 2, type: "dashed", color: css("--text-muted") } }),
             linha("Real (IPCA)", css("--series-4"), A.map(r => r.salario_real_hoje))],
    tooltip: tip(v => brl(v, 0)) });

  serieMetrica("ch-bigmac", "bigmacs");
  serieMetrica("ch-gasolina", "litros_gasolina");
  serieMetrica("ch-aluguel", "m2_aluguel_sp");

  const b = +$("base").value, rb = porAno(b);
  const idx = k => A.map(r => r[k] && rb[k] ? +(r[k] / rb[k] * 100).toFixed(1) : null);
  grafico("ch-indice", { legend: leg(), grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
    xAxis: eixoX(anos), yAxis: eixoY(null, { scale: true }),
    series: ativos().map(k => linha(M[k].nome, css(M[k].css), idx(k)))
      .concat([{ type: "line", data: [], markLine: { silent: true, symbol: "none", lineStyle: { color: css("--text-muted"), type: "dotted" }, data: [{ yAxis: 100 }], label: { show: false } } }]),
    tooltip: tip(v => fmt(v, 0)) });

  // ANP mensal: preço do litro (R$) — uma métrica por gráfico, sem eixo duplo
  const mens = D.mensal.filter(m => m.gasolina_litro);
  const cor2 = css("--series-2");
  grafico("ch-anp", { grid: { left: 8, right: 16, top: 16, bottom: 40, containLabel: true },
    xAxis: eixoX(mens.map(m => m.mes), 5), yAxis: eixoY(v => "R$ " + fmt(v, 2), { scale: true }),
    dataZoom: [{ type: "slider", height: 18, bottom: 6, borderColor: css("--border"), fillerColor: cor2 + "22", textStyle: { color: css("--text-muted") } }, { type: "inside" }],
    series: [linha("Gasolina comum (R$/l)", cor2, mens.map(m => m.gasolina_litro), area(cor2))],
    tooltip: tip((v, p) => `R$ ${fmt(v, 3)} · ${fmt(mens[p.dataIndex].litros_gasolina, 0)} litros por mínimo`) });
}
