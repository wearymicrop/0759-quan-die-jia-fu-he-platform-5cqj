import {cash,el,grid,head,pill,reason,text} from './format.js';

let record=null, revision=0;
const tabs=[...document.querySelectorAll('nav a[data-panel]')];
const feedback=message=>{el('#feedback').textContent=message;};
const current=()=>tabs.find(tab=>tab.hash===location.hash)||tabs[0];
const STATUS={funded:'已出资',unfunded:'未获出资',free:'无需出资',duplicate:'重复投递',isolated:'已隔离'};
const GATE={COMBO_BALANCE:'券组合对平',POOL_BALANCE:'预算占用对平',PAID_MATCH:'实付对平'};

async function query(path,payload){
  const response=await fetch(path,payload===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const data=await response.json();
  if(!response.ok) throw new Error(data.message||'预算请求失败');
  return data;
}

function receiptRows(rows){
  if(!rows.length) return '<p>暂无小票</p>';
  const body=rows.map(row=>{
    const denied=row.reason?`${reason(row.reason)}${row.dropped?.length?'：'+row.dropped.join('、'):''}`:'—';
    const action=row.occupied>0&&!row.voided?`<button data-void="${text(row.id)}">作废</button>`:'';
    const status=row.voided?`${STATUS[row.status]||row.status} · 已作废`:STATUS[row.status]||row.status;
    return `<tr><td>${text(row.id)}</td><td>${text(status)}</td><td>${text((row.combo||[]).join(' + ')||'—')}</td>`+
      `<td class="number">${cash(row.occupied)}</td><td class="number">${cash(row.due)}</td>`+
      `<td class="number">${cash(row.paid)}</td><td class="number">${row.gap==null?'—':cash(row.gap)}</td>`+
      `<td>${text(denied)}</td><td>${action}</td></tr>`;
  }).join('');
  return `<div class="scroll" tabindex="0"><table><thead><tr><th>小票</th><th>状态</th><th>券组合</th><th>预算占用</th><th>复算实付</th><th>实收</th><th>差额</th><th>未获优惠原因</th><th>操作</th></tr></thead><tbody>${body}</tbody></table></div>`;
}

function poolsGrid(pools){
  return grid(['门店','交易日','预算','已占用','剩余','本批占用','出资券组'],
    pools.map(row=>[row.shop,row.day,cash(row.amount),cash(row.used),cash(row.remaining),cash(row.batch_held),row.groups.join('、')]),[2,3,4,5]);
}

function findings(gate){
  if(!gate.findings.length) return '<p>全部通过</p>';
  return grid(['业务键','原因','金额'],gate.findings.map(row=>[row.id||`${row.shop} ${row.day}`,reason(row.reason),row.gap!=null?cash(row.gap):`占用 ${cash(row.used)} / 持有 ${cash(row.held)}`]),[2]);
}

function view(key){
  const d=record;
  switch(key){
    case 'receipts':
      return [head('小票裁决',pill(d.status==='formal'?'正式批次':'待复核',d.status==='formal'?'good':'bad'))+receiptRows(d.receipts),
        head('未获优惠与差额')+grid(['小票','最优组合','最优实付','被撤店券'],d.receipts.filter(row=>row.reason||row.gap).map(row=>[row.id,(row.best_combo||[]).join(' + ')||'—',cash(row.best_due),(row.dropped||[]).join('、')||'—']))];
    case 'gates':
      return [head('三道闸门',pill(d.status==='formal'?'可进入正式批次':'未对上，不得入正式批次',d.status==='formal'?'good':'bad'))+
        grid(['闸门','结果'],d.gates.map(gate=>[GATE[gate.check]||gate.check,gate.status==='PASS'?'通过':'未过']))+
        '<h3>未过明细</h3>'+d.gates.map(gate=>`<h3>${text(GATE[gate.check]||gate.check)}</h3>`+findings(gate)).join(''),
        head('裁决口径')+'<p>同店当日的店券共用一个预算池；小票按到达先后争用剩余额度，早到已核销售小票的优惠不动；容不下整张店券时撤下店券重算实付，后续仍能容纳的小票继续用券。券组合、预算占用、实付三者全部对上，批次才转为正式。</p>'];
    case 'batch':{
      const control=`<p class="hash">${text(d.id)}</p><div class="tags">${pill(d.sample)}${pill(d.reused?'重复投递 · 未重复占额':'新批次')}</div>`+
        `<form id="query-form"><label for="batch-query">按批次号恢复</label><input id="batch-query" size="48" placeholder="粘贴批次号"><button class="accent" type="submit">查询批次</button></form>`;
      return [head('批次档案')+control+head('采信统计')+grid(['物理行','采信','重传','空行','隔离'],[[d.counts.input,d.counts.accepted,d.counts.duplicates,d.counts.empty,d.counts.isolated]],[0,1,2,3,4]),
        head('作废回执')+(d.void?grid(['小票','释放额度'],d.void.released.map(row=>[row.id,cash(row.amount)]),[1])+
          (d.void.already_void.length?`<p>此前已作废：${text(d.void.already_void.join('、'))}</p>`:'')+
          (d.void.unknown.length?`<p>不在本批：${text(d.void.unknown.join('、'))}</p>`:'')
          :'<p>在「小票裁决」页对持有额度的小票执行作废，额度即刻释放回池。</p>')];
    }
    default:
      return [head('预算池')+poolsGrid(d.pools),
        head('出资口径')+'<p>预算按门店与交易日设立，只有出资券组内的券减免才占用额度；作废小票释放其占用，重传与中断重试不会重复占额。</p>'];
  }
}

function paint(){
  if(!record)return;
  const tab=current();
  tabs.forEach(node=>node===tab?node.setAttribute('aria-current','page'):node.removeAttribute('aria-current'));
  el('#section-title').textContent=tab.textContent;
  const amount=record.pools.reduce((sum,row)=>sum+row.amount,0);
  const used=record.pools.reduce((sum,row)=>sum+row.used,0);
  el('#figures').innerHTML=[['预算总额',cash(amount)],['已占用',cash(used)],['剩余额度',cash(amount-used)]]
    .map(([label,value])=>`<div class="kpi">${label}<strong>${value}</strong></div>`).join('')+
    `<div class="kpi">批次状态<strong>${record.status==='formal'?'正式':'待复核'}</strong></div>`;
  const panels=view(tab.dataset.panel);
  el('#primary-panel').innerHTML=panels[0];
  el('#side-panel').innerHTML=panels[1];
  el('#snapshot').textContent=`${record.manifest.rule_version} · ${record.id.slice(0,12)}`;
}

async function submit(){
  const seq=++revision;
  el('#submit').disabled=true;
  try{
    let payload;
    try{payload={events:el('#events').value,rules:JSON.parse(el('#rules').value),budgets:JSON.parse(el('#budgets').value)};}
    catch{feedback('规则集与预算池需为有效 JSON');return;}
    const data=await query('/api/budgets/batches',payload);
    if(seq!==revision)return;
    record=data;paint();
    feedback(data.reused?'同一批次已归档，未重复占额':data.status==='formal'?'三者对平，已进入正式批次':'存在未对上项，暂不可入正式批次');
  }catch(error){if(seq===revision)feedback(error.message);}
  finally{el('#submit').disabled=false;}
}

async function fill(){
  const sample=el('#sample').value;
  try{
    const data=await query(`/api/budgets/inputs/${sample}`);
    el('#events').value=data.events;
    el('#rules').value=JSON.stringify(data.rules,null,2);
    el('#budgets').value=JSON.stringify(data.budgets,null,2);
  }catch(error){feedback(error.message);}
}

el('#load-sample').addEventListener('click',async()=>{await fill();await submit();});
el('#submit').addEventListener('click',submit);
el('#batch-form').addEventListener('submit',event=>{event.preventDefault();submit();});
document.addEventListener('click',async event=>{
  const receipt=event.target.dataset?.void;
  if(receipt&&record){
    try{record=await query(`/api/budgets/batches/${record.id}/void`,{receipts:[receipt]});paint();feedback(`已作废 ${receipt}，额度已释放回池`);}
    catch(error){feedback(error.message);}
  }
});
document.addEventListener('submit',async event=>{
  if(event.target.id==='query-form'){
    event.preventDefault();
    const id=el('#batch-query').value.trim();
    if(!id)return;
    try{record=await query(`/api/budgets/batches/${encodeURIComponent(id)}`);paint();feedback('批次档案已恢复，记录未丢失');}
    catch(error){feedback(error.message);}
  }
});
window.addEventListener('hashchange',paint);
fill().then(submit);
