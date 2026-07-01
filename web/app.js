const fmt = n => n == null ? "" : Number(n).toLocaleString(undefined,{maximumFractionDigits:8});
const cls = a => `badge ${a || "HOLD"}`;
let selectedSymbol = null;
let chartSymbol = null;
let lastChartAt = 0;
let lastSignals = [];
let lastPositions = {};

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

function bindSelectableRows(){
  document.querySelectorAll("#signals tr,#positions tr").forEach(row => {
    row.onclick = () => {
      selectedSymbol = row.dataset.symbol;
      syncSelection();
      draw(signalForSymbol(selectedSymbol, lastSignals, lastPositions), true);
    };
  });
}

async function load(){
  document.getElementById("clock").textContent = new Date().toLocaleString();
  const [state, signals] = await Promise.all([fetch("/api/state").then(r=>r.json()), fetch("/api/signals").then(r=>r.json())]);
  lastSignals = signals;
  lastPositions = state.positions || {};
  const watch = Object.values(state.watchlist || {});
  const positions = Object.entries(state.positions || {});
  document.getElementById("watchCount").textContent = watch.length;
  document.getElementById("openCount").textContent = signals.filter(s=>s.action==="OPEN").length;
  document.getElementById("posCount").textContent = `${positions.length} / 8`;
  document.getElementById("equity").textContent = `${fmt(state.account?.equity ?? 0)} USDT`;
  const pnl = state.account?.pnl ?? 0;
  document.getElementById("pnl").textContent = `${pnl>=0?"+":""}${fmt(pnl)} USDT`;
  document.getElementById("pnl").className = pnl >= 0 ? "pos" : "neg";
  document.getElementById("signals").innerHTML = signals.map(s=>{
    const meta = state.watchlist[s.symbol] || {};
    const ch = meta.change24h ?? 0;
    return `<tr data-symbol="${s.symbol}" class="${s.symbol===selectedSymbol?"selected":""}"><td>${s.symbol}</td><td class="${ch>=0?"pos":"neg"}">${fmt(ch)}%</td><td>${meta.score??""}</td><td>${fmt(s.support)}</td><td>${fmt(s.resistance)}</td><td>${fmt(s.qvol)}</td><td>${fmt(s.volume_ratio)}</td><td><span class="${cls(s.action)}">${s.action}</span></td></tr>`;
  }).join("");
  document.getElementById("positions").innerHTML = positions.map(([sym,p])=>`<tr data-symbol="${sym}" class="${sym===selectedSymbol?"selected":""}"><td>${sym}</td><td>${fmt(p.entry)}</td><td>${fmt(p.mark)}</td><td>${fmt(p.qty)}</td><td class="neg">${fmt(p.stop)}</td><td class="${(p.gross_pnl??0)>=0?"pos":"neg"}">${(p.gross_pnl??0)>=0?"+":""}${fmt(p.gross_pnl)} USDT</td><td>${fmt(p.fee)} USDT</td><td class="${(p.pnl??0)>=0?"pos":"neg"}">${(p.pnl??0)>=0?"+":""}${fmt(p.pnl)} USDT</td></tr>`).join("");
  const selected = signalForSymbol(selectedSymbol, signals, state.positions || {}) || signals[0] || (positions[0] ? {symbol: positions[0][0], action: "POSITION"} : null);
  const nextSymbol = selected?.symbol || null;
  const shouldDrawChart = selected && (chartSymbol !== nextSymbol || Date.now() - lastChartAt > 60000);
  selectedSymbol = nextSymbol;
  syncSelection();
  bindSelectableRows();
  if(shouldDrawChart){
    await draw(selected, chartSymbol !== selectedSymbol);
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

load(); setInterval(load, 15000);
