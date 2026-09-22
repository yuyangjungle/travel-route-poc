/* UI state only. All feasibility, route generation and scores come from Python. */
'use strict';
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const state = {config:null, draft:null, results:null, mode:'ai', revision:0, busy:false};
const escapeHTML = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels = {origin_airport_ids:'出发机场',return_airport_ids:'返程机场',window_start_date:'最早出发日期',window_end_date:'最晚返回日期',min_trip_days:'最短天数',max_trip_days:'最长天数',flight_budget_cny:'机票预算'};

function message(selector, text) { const el=$(selector); el.textContent=text; el.hidden=!text; }
function progress(step) { [1,2,3].forEach(n=>$('#step-'+n).classList.toggle('active',n===step)); }
function invalidateResults() { state.results=null; $('#results').hidden=true; $('#confirmed').checked=false; }
function clearDraft() { state.revision++; state.draft=null; $('#confirmation').hidden=true; invalidateResults(); progress(1); }
function setBusy(value,button,text) {state.busy=value; button.disabled=value; button.textContent=text; button.setAttribute('aria-busy',String(value));}
async function api(path,payload) {
  const controller=new AbortController(); const timer=setTimeout(()=>controller.abort(),55000);
  try {
    const response=await fetch(path,{method:payload?'POST':'GET',headers:payload?{'Content-Type':'application/json'}:{},body:payload?JSON.stringify(payload):undefined,signal:controller.signal});
    let result;try{result=await response.json();}catch{throw new Error('服务暂时不可用，请稍后重试。');}
    if(!response.ok) throw new Error(result.error || '服务暂时不可用，请稍后重试。');
    return result;
  } catch(error) {if(error.name==='AbortError') throw new Error('本次请求超时，请稍后重试或手动填写。'); throw error;}
  finally{clearTimeout(timer);}
}
function setMode(mode) {
  state.mode=mode; $('#natural-panel').hidden=mode!=='ai'; $('#manual-panel').hidden=mode!=='manual';
  $('#mode-ai').classList.toggle('active',mode==='ai'); $('#mode-manual').classList.toggle('active',mode==='manual');
  $('#mode-ai').setAttribute('aria-pressed',String(mode==='ai')); $('#mode-manual').setAttribute('aria-pressed',String(mode==='manual'));
  message('#input-error','');
}
function initOptions(){
  for (const name of ['origin','return']) $('#'+name+'-options').innerHTML=state.config.airports.map(a=>`<label><input type="checkbox" name="${name}_airport_ids" value="${escapeHTML(a.id)}">${escapeHTML(a.name)} <small>${a.id}</small></label>`).join('');
  for (const name of ['required_destination_ids','preferred_destination_ids']) $(`[name="${name}"]`).innerHTML=state.config.destinations.map(d=>`<option value="${escapeHTML(d.id)}">${escapeHTML(d.name)}${d.in_demo_network?'':' · 暂无演示报价'}</option>`).join('');
  $('#weight-options').innerHTML=Object.entries(state.config.preferences).map(([tag,name])=>`<label>${escapeHTML(name)}<output id="weight-${tag}-value">0</output><input aria-label="${escapeHTML(name)}偏好权重" name="weight-${tag}" type="range" min="0" max="100" value="0"></label>`).join('');
  $$('#weight-options input').forEach(el=>el.addEventListener('input',()=>$('#'+el.name+'-value').value=el.value));
}
function showDraft(draft){
  state.revision++; state.draft=draft; invalidateResults();
  const f=draft.fields; const form=$('#confirm-form');
  form.reset();
  $$('#origin-options input').forEach(x=>x.checked=(f.origin_airport_ids||[]).includes(x.value));
  $$('#return-options input').forEach(x=>x.checked=(f.return_airport_ids||[]).includes(x.value));
  for(const name of ['window_start_date','window_end_date','min_trip_days','max_trip_days','flight_budget_cny','max_optional_destinations','max_connections_per_offer']) form.elements.namedItem(name).value=f[name] ?? '';
  for(const name of ['required_destination_ids','preferred_destination_ids']) [...form.elements.namedItem(name).options].forEach(o=>o.selected=(f[name]||[]).includes(o.value));
  for(const tag of Object.keys(state.config.preferences)){form.elements.namedItem('weight-'+tag).value=f.preference_weights?.[tag] ?? 0;$('#weight-'+tag+'-value').value=f.preference_weights?.[tag] ?? 0;}
  $('#intent-summary').textContent=draft.summary;
  const groups=[];
  if(draft.missing_fields?.length)groups.push(`<div class="message warning"><strong>请补充这些条件</strong><p>${draft.missing_fields.map(x=>escapeHTML(labels[x]||x)).join('、')}。下方空白字段不会自动替你猜测。</p></div>`);
  if(draft.unresolved_mentions?.length)groups.push(`<div class="message warning"><strong>需要你决定的地方</strong><ul>${draft.unresolved_mentions.map(x=>`<li>${escapeHTML(x)}</li>`).join('')}</ul></div>`);
  if(draft.assumptions?.length)groups.push(`<div class="message info"><strong>请核对这些假设</strong><ul>${draft.assumptions.map(x=>`<li>${escapeHTML(x)}</li>`).join('')}</ul></div>`);
  if(f.allow_self_transfer)groups.push('<div class="message warning"><strong>自行转机暂不支持</strong><p>你提出了自行转机要求。若愿意改为仅受保护联程，请勾选下方确认；否则请停止本次演示。</p><label><input type="checkbox" id="accept-protected-only"> 我同意改为不允许自行转机</label></div>');
  $('#assumptions').innerHTML=groups.join('');
  message('#confirm-error','');$('#confirmation').hidden=false;progress(2);$('#confirmation').scrollIntoView({behavior:'smooth'});
}
function readFields(){
  const form=$('#confirm-form'); const f={};
  f.origin_airport_ids=$$('#origin-options input:checked').map(x=>x.value);
  f.return_airport_ids=$$('#return-options input:checked').map(x=>x.value);
  if(!f.origin_airport_ids.length||!f.return_airport_ids.length)throw new Error('请至少选择一个出发机场和一个返程机场。');
  for(const key of ['window_start_date','window_end_date'])f[key]=form.elements.namedItem(key).value;
  for(const key of ['min_trip_days','max_trip_days','flight_budget_cny','max_optional_destinations','max_connections_per_offer'])f[key]=Number(form.elements.namedItem(key).value);
  for(const key of ['required_destination_ids','preferred_destination_ids'])f[key]=[...form.elements.namedItem(key).selectedOptions].map(x=>x.value);
  if(f.required_destination_ids.some(x=>f.preferred_destination_ids.includes(x)))throw new Error('必去地点与想去地点不能重复，请只保留在一侧。');
  if(f.window_start_date>f.window_end_date)throw new Error('最晚返回日期需要晚于最早出发日期。');
  if(f.min_trip_days>f.max_trip_days)throw new Error('最多天数不能小于最少天数。');
  if(state.draft.fields.allow_self_transfer && !$('#accept-protected-only')?.checked)throw new Error('当前不支持自行转机，请明确同意改为仅受保护联程，或停止本次搜索。');
  f.preference_weights=Object.fromEntries(Object.keys(state.config.preferences).map(tag=>[tag,Number(form.elements.namedItem('weight-'+tag).value)]));
  f.allow_self_transfer=false;
  return f;
}
function destinationDetail(visit){
  const info=visit.descriptive_information;const opt=visit.optimization_data;const knowledge=visit.knowledge;
  const season=knowledge?.descriptive?.best_season_months?.map(m=>m+'月').join(' / ');
  const matches=visit.preference_matches?.map(x=>state.config.preferences[x.tag_id]).join('、');
  return `<div class="destination-note"><strong>${escapeHTML(visit.destination)} · ${visit.stay_nights} 晚</strong><p>${escapeHTML(info.destination_type)} · ${escapeHTML(info.suitable_activities.join(' / '))}</p><p>停留 ${escapeHTML(visit.arrival_at.slice(0,10))} → ${escapeHTML(visit.departure_at.slice(0,10))}</p><p>优化数据：建议 ${opt.recommended_stay_nights} 晚 · 当月季节分 ${opt.season_score}/100 · 接驳 ${opt.airport_transfer_minutes} 分钟</p>${season?`<p>描述资料建议季节：${escapeHTML(season)}</p>`:''}<p>交通难度 ${escapeHTML(info.transport_difficulty)} · ${escapeHTML(info.transport_note)}</p><p>${escapeHTML(info.seasonal_note)}</p>${matches?`<p>与你的偏好对应：${escapeHTML(matches)}（基于模拟标签）</p>`:''}<p>描述资料：模拟、未核实${knowledge?.descriptive?.provenance?.updated_at?' · 更新 '+escapeHTML(knowledge.descriptive.provenance.updated_at.slice(0,10)):''}</p></div>`;
}
function renderResults(result){
  state.results=result;$('#results').hidden=false;progress(3);
  const ok=result.status==='ok';$('#no-results').hidden=ok;$('#comparison-panel').hidden=!ok;$('.results-toolbar').hidden=!ok;
  $('#result-summary').textContent=ok?`找到 ${result.summary.feasible_count} 条可行行程，${result.summary.pareto_count} 条具有不同取舍；以下展示 ${result.cards.length} 条代表方案。`:'这次条件下，当前数据还无法给出可行组合。';
  if(!ok){
    const outside=result.status==='insufficient_coverage';
    $('#no-results').innerHTML=`<h3>${outside?'这段日期还没有演示数据':'在当前模拟报价中，没有找到可行行程'}</h3><p>${outside?'演示固定报价集中在 2027 年 10 月 1–22 日。你可以手动修改日期，或载入一个示例。':'这不代表现实市场没有航班。可以一次只调整一个条件，看看放宽日期、增加预算或改变中转限制后会怎样。'}</p><p>系统没有自动放宽任何条件。必去地点即使不在本次报价子集中也不会被移除。</p>`;
    $('#route-cards').replaceChildren();
  }else{
    const columns=['方案','路线','模拟机票','较最低价','交通时间','较最少交通','新增目的地','飞行日','主要取舍'];
    $('#comparison-table').innerHTML=`<thead><tr>${columns.map(c=>`<th scope="col">${escapeHTML(c)}</th>`).join('')}</tr></thead><tbody>${result.comparison.map(row=>`<tr>${columns.map(c=>`<td>${escapeHTML(row[c])}</td>`).join('')}</tr>`).join('')}</tbody>`;
    $('#route-cards').innerHTML=result.cards.map(card=>`<article class="route-card ${card.labels.includes('best_overall')?'recommended':''}"><div class="route-top"><strong>${escapeHTML(card.label)}</strong><span>ROUTE ${String(card.rank).padStart(2,'0')}</span></div><div class="route-body"><h3 class="route-line">${escapeHTML(card.route)}</h3><div class="route-dates">${escapeHTML(card.start_date)} → ${escapeHTML(card.end_date)} · ${card.trip_days} 天 · ${card.destinations.map(v=>`${escapeHTML(v.destination)} ${v.stay_nights}晚`).join(' / ')}</div><div class="route-metrics"><div><small>模拟机票 / 人</small><strong>${escapeHTML(card.flight_cost)}</strong></div><div><small>交通总时长</small><strong>${escapeHTML(card.transit)}</strong></div><div><small>飞行日</small><strong>${card.flight_days}<span class="metric-unit"> 天</span></strong></div></div><ul class="route-reasons">${card.why.map(x=>`<li>${escapeHTML(x)}</li>`).join('')}</ul><div class="tradeoff"><b>需要接受的取舍</b>${card.tradeoffs.map(escapeHTML).join('<br>')}</div><details><summary>查看航班、停留与目的地资料</summary>${card.legs.map((leg,i)=>`<div class="leg"><strong>${escapeHTML(leg['航段'])} · ${escapeHTML(leg['价格'])}</strong><small>${escapeHTML(leg['起飞'])} → ${escapeHTML(leg['抵达'])}</small><small>${leg['中转']} 次中转 · 模拟来源 · ${escapeHTML(card.offer_sources[i]?.observed_at || '')}</small></div>`).join('')}${card.destinations.map(destinationDetail).join('')}<p class="quiet">综合 ${escapeHTML(card.overall)} · 负担 ${escapeHTML(card.burden)} · 偏好与停留收益 ${escapeHTML(card.experience)}。描述资料与优化数据独立维护。</p></details></div></article>`).join('');
  }
  $('#result-provenance').textContent=`${result.scope} 数据 v${result.dataset_version} · ${result.scoring_version} · ${result.search_complete?'搜索完整':'搜索未完成'}。`;
  $('#results').scrollIntoView({behavior:'smooth'});
}
function loadScenario(index){
  if(!state.config){message('#input-error','目录正在加载，请稍后再试。');return;}
  const scenario=state.config.scenarios[index];$('#travel-text').value=scenario.example;$('#text-count').textContent=scenario.example.length+' / 2000';
  showDraft({fields:structuredClone(scenario.fields),summary:scenario.title+'：'+scenario.intent,assumptions:['这些条件来自预设示例，可自由编辑；所有报价为模拟数据。'],unresolved_mentions:[],missing_fields:[]});
}
async function init(){
  try{
    state.config=await api('/api/bootstrap');initOptions();
    $('#coverage-text').textContent=`2027 年 10 月 · ${state.config.coverage.destination_count} 个搜索目的地 · ${state.config.coverage.offer_count} 条固定模拟报价。预算仅为单人机票，不含住宿与活动。`;
    $('#extract').disabled=!state.config.ai_available;
    $('#ai-status').textContent=state.config.ai_available?'':'AI 当前不可用。示例与手动填写仍可完成整个规划流程。';
    const icons=['☀','≈','↗','◌'];
    $('#scenario-list').innerHTML=state.config.scenarios.map((s,i)=>`<button class="scenario-button" data-scenario="${i}" type="button"><span class="scenario-icon">${icons[i]}</span><span class="scenario-name">${escapeHTML(s.title.split('｜')[0])}<small>${escapeHTML(s.title.split('｜')[1] || '从不同偏好出发')}</small></span><span class="scenario-arrow">↗</span></button>`).join('');
    $$('.scenario-button').forEach(b=>b.addEventListener('click',()=>loadScenario(Number(b.dataset.scenario))));
  }catch(error){message('#input-error','演示目录加载失败。请刷新页面重试。');$('#extract').disabled=true;$('#start-manual').disabled=true;}
}
$('#mode-ai').addEventListener('click',()=>setMode('ai'));
$('#mode-manual').addEventListener('click',()=>setMode('manual'));
$('#start-manual').addEventListener('click',()=>loadScenario(1));
$('#travel-text').addEventListener('input',()=>{$('#text-count').textContent=$('#travel-text').value.length+' / 2000';clearDraft();});
$('#assumptions').addEventListener('change',()=>{state.revision++;invalidateResults();});
$('#confirm-form').addEventListener('input',event=>{state.revision++;if(event.target.id!=='confirmed'){invalidateResults();}else{state.results=null;$('#results').hidden=true;}});
$('#extract').addEventListener('click',async()=>{
  if(state.busy)return;message('#input-error','');clearDraft();const rev=state.revision;const text=$('#travel-text').value.trim();
  if(text.length<10){message('#input-error','请至少写 10 个字符，告诉我们你的出发地、时间或旅行想法。');return;}
  setBusy(true,$('#extract'),'正在整理你的旅行想法…');
  try{const draft=await api('/api/intent',{text});if(rev===state.revision)showDraft(draft);}
  catch(error){message('#input-error',error.message);}
  finally{setBusy(false,$('#extract'),'整理我的旅行想法 →');}
});
$('#confirm-form').addEventListener('submit',async event=>{
  event.preventDefault();if(state.busy)return;message('#confirm-error','');
  try{
    const fields=readFields();if(!$('#confirmed').checked)throw new Error('请先核对并勾选确认。');
    const rev=state.revision;setBusy(true,$('#optimize'),'正在计算目的地组合与取舍…');
    const result=await api('/api/optimize',{fields,confirmed:true});
    if(rev===state.revision)renderResults(result);else message('#confirm-error','条件已修改，请重新确认并生成方案。');
  }catch(error){message('#confirm-error',error.message);}
  finally{setBusy(false,$('#optimize'),'确认条件，发现旅行组合 ↗');}
});
$('#edit-request').addEventListener('click',()=>{$('#confirmation').scrollIntoView({behavior:'smooth'});progress(2);});
$('#download-results').addEventListener('click',()=>{if(!state.results)return;const url=URL.createObjectURL(new Blob([JSON.stringify(state.results,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='travel-route-comparison.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
init();
