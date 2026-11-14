const $ = (s) => document.querySelector(s);
const brl = (v) => (v || v === 0) ? "R$ " + Number(v).toLocaleString("pt-BR") : "—";
const TIPOS = { apartamento: "Apartamento", cobertura: "Cobertura", casa: "Casa", casa_condominio: "Casa em condomínio", outro: "Outro", kitnet: "Kitnet" };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let imoveis = [];
let idsConhecidos = null;
const filtro = { texto: "", tipo: "", bairro: "", preco: "", ordem: "nota", fav: false, fora: false, desc: false, rapido: "" };

function horas(iso) { return iso ? (Date.now() - new Date(iso).getTime()) / 36e5 : 1e9; }
function ehNovo(im) { return !im.visto && horas(im.primeiro_visto) < 48; }
function baixou(im) { return im.aluguel_anterior && im.aluguel < im.aluguel_anterior && horas(im.preco_mudou_em) < 24 * 7; }
function corNota(n) { return n >= 75 ? "#2f9e44" : n >= 60 ? "#74b816" : n >= 45 ? "#f59f00" : "#c92a2a"; }
function quando(iso) {
  const h = horas(iso);
  if (h < 1) return `há ${Math.max(1, Math.round(h * 60))} min`;
  if (h < 24) return `há ${Math.round(h)} h`;
  return `há ${Math.round(h / 24)} dia(s)`;
}

async function carregar() {
  const r = await fetch("/api/imoveis");
  imoveis = await r.json();
  const ids = new Set(imoveis.filter((i) => i.passa).map((i) => i.id));
  if (idsConhecidos) {
    const novos = [...ids].filter((i) => !idsConhecidos.has(i)).length;
    if (novos) toast(`${novos} imóvel(is) novo(s) no perfil!`);
  }
  idsConhecidos = ids;
  montarBairros();
  render();
}

function montarBairros() {
  const sel = $("#f-bairro");
  const atual = sel.value;
  const bairros = [...new Set(imoveis.filter((i) => i.passa).map((i) => i.bairro).filter(Boolean))].sort();
  sel.innerHTML = '<option value="">Todos os bairros</option>' + bairros.map((b) => `<option>${esc(b)}</option>`).join("");
  sel.value = atual;
}

function filtrar() {
  const t = filtro.texto.toLowerCase();
  let l = imoveis.filter((im) => {
    if (!filtro.fora && !im.passa) return false;
    if (!filtro.desc && im.descartado) return false;
    if (!im.ativo && !im.favorito) return false;
    if (filtro.fav && !im.favorito) return false;
    if (filtro.tipo && im.tipo !== filtro.tipo) return false;
    if (filtro.bairro && im.bairro !== filtro.bairro) return false;
    if (filtro.preco && (im.custo_mensal || im.aluguel) > +filtro.preco) return false;
    if (filtro.rapido === "novos" && !ehNovo(im)) return false;
    if (filtro.rapido === "baixou" && !baixou(im)) return false;
    if (t) {
      const alvo = [im.titulo, im.bairro, im.endereco, (im.motivos || []).join(" "), im.ia?.resumo].join(" ").toLowerCase();
      if (!alvo.includes(t)) return false;
    }
    return true;
  });
  const ord = {
    nota: (a, b) => b.nota - a.nota,
    recente: (a, b) => (b.primeiro_visto || "").localeCompare(a.primeiro_visto || ""),
    preco: (a, b) => (a.custo_mensal || a.aluguel || 1e9) - (b.custo_mensal || b.aluguel || 1e9),
    area: (a, b) => (b.area || 0) - (a.area || 0),
  }[filtro.ordem];
  return l.sort(ord);
}

function render() {
  const lista = filtrar();
  const perfil = imoveis.filter((i) => i.passa && i.ativo && !i.descartado);
  $("#numeros").innerHTML = [
    ["", perfil.length, "dentro do perfil"],
    ["novos", perfil.filter(ehNovo).length, "novos (48h)"],
    ["baixou", perfil.filter(baixou).length, "baixaram de preço"],
    ["fav", imoveis.filter((i) => i.favorito).length, "favoritos"],
    ["ia", perfil.filter((i) => i.ia).length, "avaliados pela IA"],
  ].map(([k, n, txt]) => `<div class="num" data-k="${k}"><b>${n}</b><span>${txt}</span></div>`).join("");
  $("#contagem").textContent = `${lista.length} imóvel(is)` + (filtro.rapido ? ` · filtro: ${filtro.rapido} (clique de novo para tirar)` : "");
  $("#vazio").hidden = lista.length > 0;
  $("#grade").innerHTML = lista.map(card).join("");
}

function card(im) {
  const f = im.fotos || [];
  const selos = [
    ehNovo(im) && '<span class="selo novo">Novo</span>',
    baixou(im) && `<span class="selo baixou">Baixou ${brl(im.aluguel_anterior - im.aluguel)}</span>`,
    im.tipo === "cobertura" && '<span class="selo cob">Cobertura</span>',
    !im.ativo && '<span class="selo saiu">Saiu do ar?</span>',
  ].filter(Boolean).join("");
  const motivos = (im.motivos || []).filter((m) => !m.startsWith("★")).slice(0, 4)
    .map((m) => `<span class="${m.startsWith("✗") ? "mau" : ""}">${esc(m.slice(2))}</span>`).join("");
  const total = im.custo_mensal && im.custo_mensal !== im.aluguel ? `<small> · ${brl(im.custo_mensal)}/mês com tudo</small>` : "";
  const ant = im.aluguel_anterior && im.aluguel_anterior !== im.aluguel ? `<s>${brl(im.aluguel_anterior)}</s>` : "";
  return `
  <article class="card ${im.passa ? "" : "fora"} ${im.descartado ? "descartado" : ""}" data-id="${esc(im.id)}">
    <div class="foto" data-i="0" data-acao="abrir">
      ${f.length ? `<img loading="lazy" src="${esc(f[0])}" alt="" referrerpolicy="no-referrer">` : ""}
      <div class="selos">${selos}</div>
      <div class="nota" style="background:${corNota(im.nota)}" title="Nota de 0 a 100">${im.nota}</div>
      ${f.length > 1 ? `<button class="nav ant" data-acao="ant">‹</button><button class="nav prox" data-acao="prox">›</button><span class="qtd">1/${f.length}</span>` : ""}
    </div>
    <div class="info">
      <div class="tipo">${TIPOS[im.tipo] || im.tipo} · ${esc(im.bairro)}</div>
      <div class="preco">${brl(im.aluguel)}${ant}${total}</div>
      <div class="specs">
        <span>📐 ${im.area || "?"} m²</span><span>🛏 ${im.quartos ?? "?"}</span><span>🚿 ${im.banheiros ?? "?"}</span><span>🚗 ${im.vagas ?? 0}</span>
        ${im.varanda ? "<span>🌿 varanda</span>" : im.varanda === null ? "<span>🌿 ?</span>" : ""}
      </div>
      ${im.ia ? `<div class="ia"><b>IA ${im.ia.nota_geral}/10:</b> ${esc(im.ia.resumo)}</div>` : ""}
      <div class="motivos">${motivos}</div>
      <div class="acoes">
        <button class="fav ${im.favorito ? "on" : ""}" data-acao="fav">${im.favorito ? "♥ Favorito" : "♡ Favoritar"}</button>
        <button data-acao="desc">${im.descartado ? "↺ Recuperar" : "✕ Descartar"}</button>
        <a href="${esc(im.url)}" target="_blank" rel="noopener" data-acao="link">Anúncio ↗</a>
      </div>
      <div class="fonte">${im.fonte === "quintoandar" ? "QuintoAndar" : "Chaves na Mão"} · visto ${quando(im.primeiro_visto)}</div>
    </div>
  </article>`;
}

async function marcar(id, campos) {
  const im = imoveis.find((i) => i.id === id);
  Object.assign(im, campos);
  render();
  await fetch(`/api/imoveis/${encodeURIComponent(id)}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(campos) });
}

async function abrir(id) {
  marcar(id, { visto: 1 });
  const im = await (await fetch(`/api/imoveis/${encodeURIComponent(id)}`)).json();
  const ia = im.ia;
  const barra = (rot, v) => `<span>${rot}</span><div class="barra"><i style="width:${(v || 0) * 10}%"></i></div><b>${v ?? "—"}</b>`;
  const hist = (im.historico || []).map((h) => `<li>${new Date(h.data).toLocaleDateString("pt-BR")}: ${brl(h.aluguel)}</li>`).join("");
  $("#modal-corpo").innerHTML = `
    <button class="btn claro fechar" onclick="modal.close()">Fechar ✕</button>
    <div class="m-galeria">${(im.fotos || []).map((u) => `<img src="${esc(u)}" loading="lazy" referrerpolicy="no-referrer">`).join("")}</div>
    <div class="m-corpo">
      <div class="m-topo">
        <div>
          <div class="tipo">${TIPOS[im.tipo] || im.tipo} · ${esc(im.bairro)}${im.endereco ? " · " + esc(im.endereco) : ""}</div>
          <h2>${esc(im.titulo)}</h2>
          <div class="preco">${brl(im.aluguel)} <small>aluguel · cond+IPTU ${brl(im.condominio_iptu)} · ${brl(im.custo_mensal)}/mês com tudo${im.fonte === "quintoandar" ? " (taxas do QuintoAndar, sem fiança)" : " (inclui seguro fiança ≈10% e seguro incêndio estimado)"}</small></div>
          <div class="specs"><span>📐 ${im.area || "?"} m²</span><span>🛏 ${im.quartos ?? "?"} quartos</span><span>🚿 ${im.banheiros ?? "?"} banheiros</span><span>🚗 ${im.vagas ?? 0} vagas</span></div>
        </div>
        <div class="nota" style="position:static;background:${corNota(im.nota)}">${im.nota}</div>
      </div>
      ${ia ? `
      <div class="colunas">
        <div>
          <h3>Avaliação da IA pelas fotos</h3>
          <p>${esc(ia.resumo)}</p>
          <div class="barras">${barra("Beleza", ia.nota_beleza)}${barra("Modernidade", ia.nota_modernidade)}${barra("Espaço / luz", ia.nota_amplitude)}${barra("Combina c/ você", ia.nota_geral)}</div>
        </div>
        <div>
          <h3>Pontos</h3>
          <ul>${(ia.pontos_positivos || []).map((p) => `<li>👍 ${esc(p)}</li>`).join("")}${(ia.pontos_negativos || []).map((p) => `<li>👎 ${esc(p)}</li>`).join("")}</ul>
        </div>
      </div>` : `<p class="fonte">Ainda sem avaliação de IA das fotos.</p>`}
      <div class="colunas">
        <div><h3>Por que essa nota</h3><ul>${(im.motivos || []).map((m) => `<li>${esc(m)}</li>`).join("")}</ul></div>
        <div><h3>Comodidades</h3><p class="fonte">${esc((im.amenidades || []).join(" · ") || "—")}</p>
          <h3 style="margin-top:12px">Histórico de preço</h3><ul>${hist}</ul></div>
      </div>
      ${im.descricao ? `<div><h3>Descrição do anúncio</h3><div class="desc">${esc(im.descricao)}</div></div>` : ""}
      <div class="acoes">
        <button class="fav ${im.favorito ? "on" : ""}" onclick="marcar('${esc(im.id)}',{favorito:${im.favorito ? 0 : 1}});modal.close()">${im.favorito ? "♥ Tirar dos favoritos" : "♡ Favoritar"}</button>
        <a class="btn" href="${esc(im.url)}" target="_blank" rel="noopener">Abrir anúncio original ↗</a>
      </div>
    </div>`;
  modal.showModal();
}

$("#grade").addEventListener("click", (e) => {
  const alvo = e.target.closest("[data-acao]");
  const cardEl = e.target.closest(".card");
  if (!alvo || !cardEl) return;
  const id = cardEl.dataset.id;
  const im = imoveis.find((i) => i.id === id);
  const acao = alvo.dataset.acao;
  if (acao === "ant" || acao === "prox") {
    e.stopPropagation();
    const foto = cardEl.querySelector(".foto");
    const f = im.fotos || [];
    let i = (+foto.dataset.i + (acao === "prox" ? 1 : -1) + f.length) % f.length;
    foto.dataset.i = i;
    foto.querySelector("img").src = f[i];
    foto.querySelector(".qtd").textContent = `${i + 1}/${f.length}`;
  } else if (acao === "abrir") abrir(id);
  else if (acao === "fav") marcar(id, { favorito: im.favorito ? 0 : 1 });
  else if (acao === "desc") marcar(id, { descartado: im.descartado ? 0 : 1 });
  else if (acao === "link") marcar(id, { visto: 1 });
});

$("#numeros").addEventListener("click", (e) => {
  const k = e.target.closest(".num")?.dataset.k;
  if (k === undefined) return;
  if (k === "fav") { filtro.fav = !filtro.fav; $("#f-fav").checked = filtro.fav; }
  else filtro.rapido = filtro.rapido === k || !k ? "" : k;
  render();
});

$("#f-texto").addEventListener("input", (e) => { filtro.texto = e.target.value; render(); });
$("#f-bairro").addEventListener("change", (e) => { filtro.bairro = e.target.value; render(); });
$("#f-preco").addEventListener("input", (e) => { filtro.preco = e.target.value; render(); });
$("#f-ordem").addEventListener("change", (e) => { filtro.ordem = e.target.value; render(); });
for (const [id, k] of [["#f-fav", "fav"], ["#f-fora", "fora"], ["#f-desc", "desc"]])
  $(id).addEventListener("change", (e) => { filtro[k] = e.target.checked; render(); });
$("#f-tipo").addEventListener("click", (e) => {
  const b = e.target.closest(".chip"); if (!b) return;
  document.querySelectorAll("#f-tipo .chip").forEach((c) => c.classList.toggle("ativo", c === b));
  filtro.tipo = b.dataset.v; render();
});
$("#btn-varrer").addEventListener("click", async () => {
  await fetch("/api/varrer", { method: "POST" });
  toast("Varredura iniciada — leva alguns minutos");
  setTimeout(status, 1500);
});
const modal = $("#modal");
modal.addEventListener("click", (e) => { if (e.target === modal) modal.close(); });

let estavaRodando = false;
async function status() {
  try {
    const s = await (await fetch("/api/status")).json();
    const el = $("#status");
    el.classList.toggle("rodando", !!s.rodando);
    $("#btn-varrer").disabled = !!s.rodando;
    if (s.rodando) el.textContent = s.etapa || "Varrendo…";
    else {
      const u = s.ultima_execucao;
      const prox = s.proxima ? new Date(s.proxima).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }) : "—";
      el.textContent = u ? `Última varredura ${quando(u.fim)} (${u.novos} novos) · próxima às ${prox}` : "Aguardando primeira varredura";
    }
    if (estavaRodando && !s.rodando) carregar();
    estavaRodando = !!s.rodando;
  } catch { $("#status").textContent = "robô desconectado"; }
}

function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 4000);
}

carregar(); status();
setInterval(status, 10000);
setInterval(carregar, 120000);
