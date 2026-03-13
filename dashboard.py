"""
Веб-дашборд для Paper Trader
Запускается вместе с ботом, доступен по URL Railway
"""

import json
import os
import fcntl
from datetime import datetime
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

STATE_FILE = "paper_state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                return json.load(f)
            except Exception:
                return {}
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    return {}

@app.route("/")
def dashboard():
    return """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Paper Trader Dashboard</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;600;700&display=swap');
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#050a07;color:#7a9e8a;font-family:'JetBrains Mono',monospace;padding:20px}
  ::-webkit-scrollbar{width:4px}::-webkit-scrollbar-thumb{background:#0a2a1a}
  h1{color:#c0f0d0;font-size:18px;letter-spacing:3px;margin-bottom:4px}
  .sub{font-size:10px;color:#2a4a3a;letter-spacing:2px;margin-bottom:24px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:20px}
  .card{background:#060c08;border:1px solid #0a1f0f;border-radius:6px;padding:14px}
  .card-label{font-size:9px;color:#2a4a3a;letter-spacing:2px;margin-bottom:6px}
  .card-value{font-size:22px;font-weight:700;color:#c0f0d0}
  .card-value.green{color:#00ff88}
  .card-value.red{color:#ff3355}
  .card-value.yellow{color:#f0c040}
  .section{background:#060c08;border:1px solid #0a1f0f;border-radius:6px;padding:14px;margin-bottom:12px}
  .section-title{font-size:9px;color:#2a6a3a;letter-spacing:2px;margin-bottom:12px;border-bottom:1px solid #0a1f0f;padding-bottom:8px}
  table{width:100%;border-collapse:collapse;font-size:10px}
  th{color:#2a4a3a;text-align:left;padding:4px 8px;font-size:8px;letter-spacing:1px;border-bottom:1px solid #0a1f0f}
  td{padding:6px 8px;border-bottom:1px solid #060c08;color:#7a9e8a}
  tr.win td:first-child{border-left:3px solid #00ff88}
  tr.loss td:first-child{border-left:3px solid #ff3355}
  .badge{padding:2px 8px;border-radius:3px;font-size:8px;font-weight:700}
  .badge.win{background:#0a2a0f;color:#00ff88}
  .badge.loss{background:#1a0608;color:#ff3355}
  .pos-card{background:#061509;border:1px solid #00ff8820;border-radius:6px;padding:14px;margin-bottom:12px}
  .pos-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px}
  .pos-item{background:#050a07;border-radius:4px;padding:8px}
  .pos-item-label{font-size:8px;color:#2a4a3a;letter-spacing:1px}
  .pos-item-value{font-size:13px;font-weight:600;margin-top:2px}
  .bar-wrap{background:#0a1f0f;border-radius:2px;height:6px;margin-top:4px;overflow:hidden}
  .bar{height:100%;border-radius:2px;transition:width 0.5s}
  .refresh{font-size:9px;color:#2a4a3a;text-align:right;margin-bottom:12px}
  .dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px;animation:pulse 1.5s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:0.3}}
  .pnl-chart{display:flex;align-items:flex-end;gap:3px;height:60px;margin-top:8px}
  .pnl-bar{flex:1;border-radius:2px;min-height:3px}
  footer{text-align:center;font-size:8px;color:#1a3a2a;margin-top:24px;padding-top:12px;border-top:1px solid #0a1f0f}
</style>
</head>
<body>
<h1>⬡ PAPER TRADER</h1>
<div class="sub">ВИРТУАЛЬНЫЙ БАЛАНС — РЕАЛЬНЫЕ ЦЕНЫ BYBIT</div>
<div class="refresh" id="refresh-time">Обновляется каждые 30 сек</div>

<div id="app">Загрузка...</div>

<footer>Paper Trader — симуляция торговли. Реальные деньги не используются.</footer>

<script>
async function load(){
  document.getElementById('refresh-time').textContent='Обновление...';
  document.getElementById('refresh-time').style.color='#2a4a3a';
  try{
    const r=await fetch('/api/state');
    const d=await r.json();
    render(d);
    document.getElementById('refresh-time').textContent='Обновлено: '+new Date().toLocaleTimeString('ru');
    document.getElementById('refresh-time').style.color='#2a6a3a';
  }catch(e){
    console.error("Ошибка загрузки состояния:", e);
    document.getElementById('app').innerHTML=`<div style="color:#ff3355;padding:20px">Ошибка загрузки данных<br>${e.message}</div>`;
  }
}

function fmt(v){
  if(!v && v!==0) return '–';
  if(Math.abs(v)>=1000) return v.toLocaleString('ru',{minimumFractionDigits:2,maximumFractionDigits:2});
  if(Math.abs(v)>=1)    return v.toFixed(3);
  if(Math.abs(v)>=0.1)  return v.toFixed(4);
  if(Math.abs(v)>=0.0001) return v.toFixed(6);
  return v.toFixed(10);  // PEPE, SHIB, BONK
}

function render(d){
  const bal=d.balance||2000;
  const start=d.start_balance||2000;
  const pnl=d.total_pnl||0;
  const pnlPct=((bal-start)/start*100).toFixed(2);
  const trades=d.trades||0;
  const wins=d.wins||0;
  const wr=trades?Math.round(wins/trades*100):0;
  const log=d.trade_log||[];
  const pos=d.position;

  let html='';

  // Stats cards
  html+=`<div class="grid">
    <div class="card">
      <div class="card-label">ВИРТУАЛЬНЫЙ БАЛАНС</div>
      <div class="card-value">$${bal.toLocaleString('ru',{minimumFractionDigits:2})}</div>
    </div>
    <div class="card">
      <div class="card-label">ОБЩИЙ PnL</div>
      <div class="card-value ${pnl>=0?'green':'red'}">${pnl>=0?'+':''}${pnl.toFixed(2)}$<br>
        <span style="font-size:13px">(${pnlPct>=0?'+':''}${pnlPct}%)</span></div>
    </div>
    <div class="card">
      <div class="card-label">СДЕЛОК / ЛИМИТ</div>
      <div class="card-value yellow">${trades} <span style="font-size:13px;color:#2a4a3a">/ 15</span></div>
    </div>
    <div class="card">
      <div class="card-label">ВИНРЕЙТ</div>
      <div class="card-value ${wr>=50?'green':'red'}">${wr}%
        <span style="font-size:11px;color:#2a4a3a">${wins}W / ${trades-wins}L</span>
      </div>
      <div class="bar-wrap"><div class="bar" style="width:${wr}%;background:${wr>=50?'#00ff88':'#ff3355'}"></div></div>
    </div>
    <div class="card">
      <div class="card-label">ЛУЧШАЯ СДЕЛКА</div>
      <div class="card-value green">+${(d.best_trade||0).toFixed(2)}$</div>
    </div>
    <div class="card">
      <div class="card-label">ХУДШАЯ СДЕЛКА</div>
      <div class="card-value red">${(d.worst_trade||0).toFixed(2)}$</div>
    </div>
  </div>`;

  // Active position
  if(pos){
    const curPrice = pos.current_price || d.current_price || pos.entry;
    const direction = pos.direction || "LONG";
    let priceDiff = curPrice - pos.entry;
    if(direction === "SHORT") priceDiff = -priceDiff;

    const unrealGross = priceDiff * pos.qty;
    const commissionEst = ((pos.usdt||0) + unrealGross) * 0.001;  // только выход
    const unrealNet = unrealGross - commissionEst;
    const unrealPct = (priceDiff / pos.entry * 100).toFixed(2);
    const unrealColor = unrealNet >= 0 ? '#00ff88' : '#ff3355';

    const toTp = direction === "LONG"
      ? ((pos.tp - curPrice) / curPrice * 100).toFixed(1)
      : ((curPrice - pos.tp) / curPrice * 100).toFixed(1);
    const toSl = direction === "LONG"
      ? ((curPrice - pos.sl) / curPrice * 100).toFixed(1)
      : ((pos.sl - curPrice) / curPrice * 100).toFixed(1);

    html+=`<div class="pos-card">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div><span class="dot" style="background:#00ff88"></span><span style="color:#c0f0d0;font-weight:700;font-size:13px">${pos.symbol} (${direction})</span></div>
        <span class="badge win">ОТКРЫТА</span>
      </div>
      <div class="pos-grid">
        <div class="pos-item"><div class="pos-item-label">ВХОД</div><div class="pos-item-value" style="color:#f0c040">${fmt(pos.entry)}</div></div>
        <div class="pos-item"><div class="pos-item-label">ТЕКУЩАЯ</div><div class="pos-item-value" style="color:#c0f0d0">${fmt(curPrice)}</div></div>
        <div class="pos-item"><div class="pos-item-label">TP</div><div class="pos-item-value" style="color:#00ff88">${fmt(pos.tp)} <small style="color:#2a6a3a">(ещё +${toTp}%)</small></div></div>
        <div class="pos-item"><div class="pos-item-label">SL</div><div class="pos-item-value" style="color:#ff3355">${fmt(pos.sl)} <small style="color:#4a1a2a">(-${toSl}% до)</small></div></div>
      </div>
      <div style="margin:10px 0;padding:10px;background:#050a07;border-radius:6px;display:flex;justify-content:space-between;align-items:center;border:1px solid ${unrealColor}40">
        <span style="font-size:9px;color:#2a4a3a;letter-spacing:1px">НЕРЕАЛИЗ. PnL</span>
        <span style="font-size:17px;font-weight:700;color:${unrealColor}">${unrealNet>=0?'+':''}${unrealNet.toFixed(2)}$ (${unrealPct>=0?'+':''}${unrealPct}%)</span>
      </div>
      <div style="font-size:8px;color:#2a4a3a;margin-top:4px">
        Открыта: ${pos.opened_at?.slice(0,19).replace('T',' ')||'–'} | Объём: $${(pos.usdt||0).toFixed(0)}
        <br>Цена обновлена: ${new Date().toLocaleTimeString('ru')}
      </div>
    </div>`;
  }

  // PnL chart
  if(log.length>1){
    let running=start;
    const points=[start,...log.map(t=>{running+=t.pnl;return running;})].slice(-20);
    // Всегда добавляем текущий баланс с учётом открытой позиции
    const currentBal = bal + (d.position?.unreal_pnl || 0);
    points.push(currentBal);
    const minV=Math.min(...points), maxV=Math.max(...points)||start+1;
    html+=`<div class="section"><div class="section-title">ДИНАМИКА БАЛАНСА</div>
      <div class="pnl-chart">
        ${points.map(v=>{
          const h=Math.max(3,((v-minV)/(maxV-minV||1))*56+4);
          const up=v>=start;
          return `<div class="pnl-bar" style="height:${h}px;background:${up?'#00ff8860':'#ff335560'}"></div>`;
        }).join('')}
      </div>
    </div>`;
  }

  // Trade log
  if(log.length>0){
    const rows=log.slice().reverse().slice(0,30).map(t=>`
      <tr class="${t.result==='WIN'?'win':'loss'}">
        <td>${t.time?.slice(0,16)||'–'}</td>
        <td style="color:#c0f0d0;font-weight:600">${t.symbol}</td>
        <td>${fmt(t.entry)}</td>
        <td>${fmt(t.exit)}</td>
        <td style="color:${t.pnl>=0?'#00ff88':'#ff3355'};font-weight:700">${t.pnl>=0?'+':''}${t.pnl.toFixed(4)}$</td>
        <td><span class="badge ${t.result==='WIN'?'win':'loss'}">${t.result}</span></td>
      </tr>`).join('');
    html+=`<div class="section"><div class="section-title">ИСТОРИЯ СДЕЛОК (последние 30)</div>
      <table><thead><tr>
        <th>ВРЕМЯ</th><th>ПАРА</th><th>ВХОД</th><th>ВЫХОД</th><th>PnL</th><th>ИТОГ</th>
      </tr></thead><tbody>${rows}</tbody></table></div>`;
  } else {
    html+=`<div class="section"><div class="section-title">ИСТОРИЯ СДЕЛОК</div>
      <div style="text-align:center;padding:20px;color:#2a4a3a;font-size:11px">Сделок ещё нет — бот ищет сигнал...</div></div>`;
  }

  document.getElementById('app').innerHTML=html;
}

load();
setInterval(load, 30000);
</script>
</body>
</html>"""

@app.route("/api/state")
def api_state():
    state = load_state()
    return jsonify(state)

@app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.utcnow().isoformat()})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
