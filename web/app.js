const fmt = n => {
  const v = Number(n);
  return Number.isFinite(v) ? v.toLocaleString(undefined,{maximumFractionDigits:8}) : "";
};
const compact = (n, digits=4) => {
  const v = Number(n);
  return Number.isFinite(v) ? v.toLocaleString(undefined,{maximumFractionDigits:digits}) : "";
};
const price = n => compact(n, 6);
const qty = n => compact(n, 4);
const usdt = n => compact(n, 2);
const money = n => Number.isFinite(Number(n)) ? `${Number(n)>=0?"+":""}${compact(n, 4)} USDT` : "";
const cls = a => `badge ${a || "HOLD"}`;
let selectedSymbol = null;
let chartSymbol = null;
let lastChartAt = 0;
let lastSignals = [];
let lastPositions = {};
let lastState = null;
let lastHistory = [];
let dashboardLoadInFlight = false;
let renderFrame = null;

function setText(id, value){
  const node = document.getElementById(id);
  if(node && node.textContent !== String(value)) node.textContent = value;
}

function setClassName(id, value){
  const node = document.getElementById(id);
  if(node && node.className !== value) node.className = value;
}

function setHtmlIfChanged(id, html){
  const node = document.getElementById(id);
  if(node && node.dataset.html !== html){
    node.innerHTML = html;
    node.dataset.html = html;
  }
}

function signalForSymbol(symbol, signals, positions){
  if(!symbol) return null;
  const signal = signals.find(s => s.symbol === symbol);
  if(signal) return signal;
  if(positions[symbol]) return {symbol, action: "POSITION"};
  return null;
}

function syncSelection(){
  document.querySelectorAll("#signals tr,#positions tr").forEach(row => {
    row.classList.toggle("selected", row.dataset.symbol === selectedSymbol);
  });
}

function bindSelectableTables(){
  document.querySelector(".grid").addEventListener("click", event => {
    const row = event.target.closest("#signals tr,#positions tr");
    if(!row) return;
    selectedSymbol = row.dataset.symbol;
    syncSelection();
    draw(signalForSymbol(selectedSymbol, lastSignals, lastPositions), true);
  });
}

function eventTime(row){
  const ts = row.closed_at || row.opened_at || row.created_at;
  return ts ? new Date(ts * 1000).toLocaleString() : "";
}

function eventFee(row){
  return row.entry_fee ?? row.fee ?? "";
}

function eventPnl(row){
  return Number.isFinite(Number(row.gross_pnl)) ? money(row.gross_pnl) : "";
}

function eventReason(row){
  const reason = row.reason || row.reasons || [];
  return Array.isArray(reason) ? reason.join(", ") : reason;
}

async function loadDashboard(){
  if(dashboardLoadInFlight) return;
  dashboardLoadInFlight = true;
  const startedAt = Date.now();
  try{
    const snapshot = await fetch("/api/dashboard").then(r=>r.json());
    lastState = snapshot.state;
    lastHistory = snapshot.history || [];
    lastSignals = snapshot.signals || [];
    scheduleRender(snapshot.updated_at);
  }finally{
    dashboardLoadInFlight = false;
    setTimeout(loadDashboard, Math.max(0, 1000 - (Date.now() - startedAt)));
  }
}

function scheduleRender(updatedAt){
  if(renderFrame) cancelAnimationFrame(renderFrame);
  renderFrame = requestAnimationFrame(() => {
    renderFrame = null;
    render(updatedAt);
  });
}

function render(updatedAt){
  const state = lastState;
  if(!state) return;
  lastPositions = state.positions || {};
  const signals = lastSignals;
  const history = lastHistory;
  const watch = Object.values(state.watchlist || {});
  const positions = Object.entries(state.positions || {});
  const pnl = state.account?.pnl ?? 0;
  const clockText = updatedAt ? new Date(updatedAt * 1000).toLocaleString() : new Date().toLocaleString();
  const signalsHtml = signals.map(s=>{
    const meta = state.watchlist[s.symbol] || {};
    const ch = meta.change24h ?? 0;
    return `<tr data-symbol="${s.symbol}" class="${s.symbol===selectedSymbol?"selected":""}"><td>${s.symbol}</td><td class="${ch>=0?"pos":"neg"}">${fmt(ch)}%</td><td>${meta.score??""}</td><td>${fmt(s.support)}</td><td>${fmt(s.resistance)}</td><td>${fmt(s.qvol)}</td><td>${fmt(s.volume_ratio)}</td><td><span class="${cls(s.action)}">${s.action}</span></td></tr>`;
  }).join("");
  const positionsHtml = positions.map(([sym,p])=>{
    const stale = p.mark == null || p.market_status !== "TRADING";
    const mark = stale ? `<span class="warn">${p.market_status || "NO MARK"}</span>` : price(p.mark);
    const tp = Number.isFinite(Number(p.take_profit)) ? price(p.take_profit) : "";
    return `<tr data-symbol="${sym}" class="${sym===selectedSymbol?"selected":""} ${stale?"stale":""}" title="${p.mark_error || ""}"><td>${sym}</td><td>${usdt(p.notional)} USDT</td><td>${price(p.entry)}</td><td>${mark}</td><td class="neg">${price(p.stop)}</td><td class="pos">${tp}</td><td>${usdt(p.fee)} USDT</td><td class="${(p.pnl??0)>=0?"pos":"neg"}">${money(p.pnl)}</td></tr>`;
  }).join("");
  const historyHtml = history.slice(0, 50).map(row=>{
    const action = row.action === "CLOSE" ? "CLOSE" : "BUY";
    const pnl = Number(row.gross_pnl);
    return `<tr><td>${eventTime(row)}</td><td>${row.symbol || ""}</td><td><span class="${cls(action === "BUY" ? "OPEN" : "EXIT")}">${action}</span></td><td>${price(row.price)}</td><td>${usdt(row.notional)} USDT</td><td>${usdt(eventFee(row))} USDT</td><td class="${Number.isFinite(pnl) ? (pnl >= 0 ? "pos" : "neg") : ""}">${eventPnl(row)}</td><td>${eventReason(row)}</td></tr>`;
  }).join("");
  setText("clock", clockText);
  setText("watchCount", watch.length);
  setText("openCount", signals.filter(s=>s.action==="OPEN").length);
  setText("posCount", `${positions.length} / ${state.account?.slots ?? 10}`);
  setText("equity", `${fmt(state.account?.equity ?? 0)} USDT`);
  setText("pnl", `${pnl>=0?"+":""}${fmt(pnl)} USDT`);
  setClassName("pnl", pnl >= 0 ? "pos" : "neg");
  setHtmlIfChanged("signals", signalsHtml);
  setHtmlIfChanged("positions", positionsHtml);
  setHtmlIfChanged("history", historyHtml);
  const selected = signalForSymbol(selectedSymbol, signals, state.positions || {}) || signals[0] || (positions[0] ? {symbol: positions[0][0], action: "POSITION"} : null);
  const nextSymbol = selected?.symbol || null;
  const shouldDrawChart = selected && (chartSymbol !== nextSymbol || Date.now() - lastChartAt > 60000);
  selectedSymbol = nextSymbol;
  syncSelection();
  if(shouldDrawChart){
    draw(selected, chartSymbol !== selectedSymbol);
  }else{
    document.getElementById("chartTitle").textContent = selected ? `Levels · ${selected.symbol} · ${selected.action}` : "Levels";
  }
}

async function draw(s, showLoading=true){
  document.getElementById("chartTitle").textContent = s ? `Levels · ${s.symbol} · ${s.action}` : "Levels";
  const box = document.getElementById("tvChart");
  if(!s){ box.innerHTML = ""; chartSymbol = null; return; }
  if(showLoading){
      box.innerHTML = `<div class="chartLoading">Loading ${s.symbol} 5m candles...</div>`;
  }
  try{
    const data = await fetch(`/api/klines?symbol=${encodeURIComponent(s.symbol)}&interval=5m&limit=96`).then(r=>{
      if(!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    });
    if(data.error) throw new Error(data.error);
    renderCandles(box, data, s);
    chartSymbol = s.symbol;
    lastChartAt = Date.now();
  }catch(err){
    if(showLoading || chartSymbol !== s.symbol){
      box.innerHTML = `<div class="chartError">K-line unavailable: ${err.message}</div>`;
    }
  }
}

function renderCandles(box, data, signal){
  const rows = data.rows || [];
  if(!rows.length){ box.innerHTML = `<div class="chartError">No candles for ${data.symbol || signal.symbol}</div>`; return; }
  const w = box.clientWidth || 640;
  const h = box.clientHeight || 420;
  const pad = {l:54, r:18, t:18, b:34};
  const innerW = w - pad.l - pad.r;
  const innerH = h - pad.t - pad.b;
  const highs = rows.map(r=>r.h);
  const lows = rows.map(r=>r.l);
  const levelVals = [signal.support, signal.resistance, data.levels?.support, data.levels?.resistance].filter(v=>Number.isFinite(Number(v))).map(Number);
  let min = Math.min(...lows, ...levelVals);
  let max = Math.max(...highs, ...levelVals);
  const span = max - min || max || 1;
  min -= span * 0.06;
  max += span * 0.06;
  const y = v => pad.t + (max - v) / (max - min) * innerH;
  const x = i => pad.l + (i + 0.5) / rows.length * innerW;
  const cw = Math.max(2, Math.min(9, innerW / rows.length * 0.62));
  const grid = [0, .25, .5, .75, 1].map(pct => {
    const gy = pad.t + pct * innerH;
    const price = max - pct * (max - min);
    return `<line x1="${pad.l}" y1="${gy}" x2="${w-pad.r}" y2="${gy}" class="gridLine"/><text x="8" y="${gy+4}" class="axisText">${fmt(price)}</text>`;
  }).join("");
  const candles = rows.map((r,i)=>{
    const cx = x(i);
    const up = r.c >= r.o;
    const color = up ? "#31d158" : "#ff453a";
    const top = y(Math.max(r.o, r.c));
    const bottom = y(Math.min(r.o, r.c));
    const bodyH = Math.max(1, bottom - top);
    return `<line x1="${cx}" y1="${y(r.h)}" x2="${cx}" y2="${y(r.l)}" stroke="${color}" stroke-width="1"/><rect x="${cx-cw/2}" y="${top}" width="${cw}" height="${bodyH}" fill="${color}" rx="1"/>`;
  }).join("");
  const line = (value, label, klass) => Number.isFinite(Number(value)) ? `<line x1="${pad.l}" y1="${y(Number(value))}" x2="${w-pad.r}" y2="${y(Number(value))}" class="${klass}"/><text x="${pad.l+8}" y="${y(Number(value))-6}" class="levelText">${label} ${fmt(value)}</text>` : "";
  const first = new Date(rows[0].t * 1000).toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"});
  const last = new Date(rows[rows.length-1].t * 1000).toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"});
  box.innerHTML = `<svg viewBox="0 0 ${w} ${h}" width="100%" height="100%" role="img" aria-label="${data.symbol} 5 minute candlestick chart">${grid}<line x1="${pad.l}" y1="${pad.t}" x2="${pad.l}" y2="${h-pad.b}" class="axisLine"/><line x1="${pad.l}" y1="${h-pad.b}" x2="${w-pad.r}" y2="${h-pad.b}" class="axisLine"/>${candles}${line(data.levels?.support ?? signal.support, "Support", "supportLine")}${line(data.levels?.resistance ?? signal.resistance, "Resistance", "resistanceLine")}<text x="${pad.l}" y="${h-10}" class="axisText">${first}</text><text x="${w-pad.r-44}" y="${h-10}" class="axisText">${last}</text></svg>`;
}

bindSelectableTables();
loadDashboard();
