// Página inicial: monta "sobre", stack (logos) e experiência a partir de data/perfil.json
const EXTRA = { sql: { t: "SQL", hex: "#336791", txt: "SQL" }, pyspark: { t: "PySpark", hex: "#E25A1C", ref: "apachespark" },
  powerbi: { t: "Power BI", hex: "#F2C811", txt: "BI" } };
const lum = (hex) => { const n = parseInt(hex.slice(1), 16), c = [n >> 16, (n >> 8) & 255, n & 255].map(v => { v /= 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }); return .2126 * c[0] + .7152 * c[1] + .0722 * c[2]; };

function tile(slug) {
  const x = EXTRA[slug], l = LOGOS[slug] || (x && x.ref && LOGOS[x.ref]);
  const titulo = x ? x.t : l ? l.t : slug, hex = x ? x.hex : l ? l.hex : "#888888";
  const escuro = lum(hex) < .04, claro = lum(hex) > .6;
  const icon = l && !(x && x.txt) ? `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${l.d}"/></svg>` : `<div class="txt-logo">${x ? x.txt : titulo}</div>`;
  return `<div class="logo-tile${escuro ? " dark-invert" : ""}" style="--tile:${claro ? "#c9a400" : hex}" title="${titulo}">${icon}<span>${titulo}</span></div>`;
}

fetch("data/perfil.json").then(r => r.json()).then(p => {
  document.getElementById("nome").textContent = p.nome;
  document.getElementById("titulo").textContent = p.titulo;
  document.getElementById("resumo").textContent = p.resumo;
  const gh = document.getElementById("lnk-github"), li = document.getElementById("lnk-linkedin");
  if (p.links.github) gh.href = p.links.github; else gh.remove();
  if (p.links.linkedin) li.href = p.links.linkedin; else li.remove();
  document.getElementById("areas").innerHTML = p.areas.map(a => `<div class="card"><h3>${a.titulo}</h3><p class="small muted" style="margin:0">${a.texto}</p></div>`).join("");
  document.getElementById("stack").innerHTML = p.stack.map(g => `<div class="stack-group"><h3>${g.grupo}</h3><div class="logos">${g.itens.map(tile).join("")}</div></div>`).join("");
  const xp = document.getElementById("xp");
  if (p.experiencia.length) xp.innerHTML = p.experiencia.map(e => `<div class="xp"><div class="when">${e.periodo}</div><div><b>${e.cargo}</b> · ${e.empresa}<p class="small muted" style="margin:4px 0 0">${e.texto || ""}</p></div></div>`).join("");
  else document.getElementById("sec-xp").remove();
  document.getElementById("hero-logos").innerHTML = ["python", "databricks", "snowflake", "amazonwebservices", "googlecloud", "apachespark"].map(tile).join("");
});
document.getElementById("ano-atual").textContent = new Date().getFullYear();
