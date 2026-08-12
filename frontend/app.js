const state = { dashboard: null, selectedId: null, detail: null, loading: false, query: '', activeTab: 'job', backendOnline: null, lastBackendError: '', profile: null, preferences: null, drawer: null };
const $ = (sel) => document.querySelector(sel);
const esc = (v='') => String(v ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));

function toast(message, error=false){
  const el=$('#toast'); el.textContent=message; el.className='toast show'+(error?' error':'');
  clearTimeout(toast._t); toast._t=setTimeout(()=>el.className='toast',3200);
}
function setBackendState(ok,message=''){
  state.backendOnline=ok; state.lastBackendError=ok?'':message;
  if(state.dashboard) renderStatus();
}
async function requestJSON(url, options={}, quiet=false){
  const controller=new AbortController();
  const {timeoutMs=8000,...fetchOptions}=options;
  const timer=setTimeout(()=>controller.abort(),timeoutMs);
  try{
    const r=await fetch(url,{cache:'no-store',...fetchOptions,signal:controller.signal});
    const text=await r.text();
    let j={};
    try{j=text?JSON.parse(text):{};}catch{throw new Error(`后端返回了非 JSON 响应（HTTP ${r.status}）`);}
    if(!r.ok) throw new Error(j.reason||j.error||`HTTP ${r.status}`);
    setBackendState(true); return j;
  }catch(e){
    const message=e?.name==='AbortError'?'Dashboard 请求超时':(e?.message||'Dashboard 后端未响应');
    setBackendState(false,message);
    if(!quiet && e instanceof TypeError) throw new Error('Dashboard 后端未响应（127.0.0.1:8799），正在等待服务恢复');
    throw new Error(message);
  }finally{clearTimeout(timer);}
}
async function getJSON(url,quiet=false){ return requestJSON(url,{},quiet); }
async function postJSON(url,body={}){ return requestJSON(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); }

function statusChip(label, ok=true, detail=''){
  return `<div class="status-chip ${ok?'ok':'warn'}" title="${esc(detail)}"><span class="status-dot"></span>${esc(label)}</div>`;
}
function renderStatus(){
  const target=$('#system-status');
  const d=state.dashboard;if(!d||!target)return;
  const rt=d.runtime||{}, a=d.agent_status||{}, c=d.counts||{};
  target.innerHTML=[
    statusChip(`服务 ${state.backendOnline===false?'离线':'在线'}`,state.backendOnline!==false,state.lastBackendError||'Dashboard API :8799'),
    statusChip(`BOSS ${rt.boss_9227_online?'在线':'待连接'}`,!!rt.boss_9227_online),
    statusChip(`L0 ${c.l0_complete||0}`,true),
    statusChip(`AI ${a.last_ai_agent?'已连接':'待连接'}`,!!a.last_ai_agent,a.last_ai_agent||''),
    statusChip(`L3 ${c.l3_complete||0}`,true),
    statusChip(`今日 ${c.today_top||0}`,true),
  ].join('');
}
function cardScore(j){ return j.final_score ?? j.provisional_score ?? j.l2_score ?? '—'; }
function jobCard(j,index,isTop){
  const active=state.selectedId===j.job_id?' active':'';
  const score=cardScore(j);
  const favorite=j.ui_state?.favorite===true;
  const tags=(j.highlights||[]).slice(0,3).map((x,i)=>`<span class="mini-tag ${i===0?'hot':''}">${esc(x)}</span>`).join('');
  const pending=(j.constraint_state?.pending||[]).length?`<span class="mini-tag warn">${esc(j.constraint_state.pending[0])}</span>`:'';
  const held=j.ui_state?.state==='hold'?'<span class="mini-tag warn">已暂缓</span>':'';
  const badge=isTop?`<span class="rank-no">${index+1}</span>`:`<span>${esc(Math.round(score))}</span>`;
  return `<article class="job-card${active}${favorite?' favorite':''}" data-job-id="${esc(j.job_id)}" style="--accent:${isTop?'rgba(185,255,79,.14)':'rgba(157,123,255,.14)'};--accent-text:${isTop?'var(--lime)':'#b9a8ff'}">
    ${favorite?'<div class="card-favorite" title="已收藏">★</div>':''}
    <div class="score-orb">${badge}</div>
    <div class="job-main">
      <div class="job-title-line">${esc(j.company)} <span class="muted">|</span> ${esc(j.title)}</div>
      <div class="job-meta">${esc(j.salary||'薪资待确认')} · ${esc(j.location||'地点未知')} · 匹配 ${esc(score)}</div>
      <div class="tag-row">${tags}${pending}${held}</div>
    </div>
  </article>`;
}
function bindCards(){ document.querySelectorAll('.job-card').forEach(el=>el.addEventListener('click',()=>selectJob(el.dataset.jobId))); }
function renderLists(){
  const d=state.dashboard||{}; const rawTop=d.today_top||[], rawQ=d.qualified||[], rawFav=d.favorites||[];
  const match=j=>!state.query||[j.company,j.title,j.salary,j.location,...(j.highlights||[])].join(' ').toLowerCase().includes(state.query.toLowerCase());
  const fav=rawFav.filter(match), favIds=new Set(fav.map(x=>x.job_id));
  const top=rawTop.filter(match).filter(x=>!favIds.has(x.job_id)), q=rawQ.filter(match).filter(x=>!favIds.has(x.job_id));
  $('#top-count').textContent=rawTop.filter(match).length; $('#qualified-count').textContent=rawQ.filter(match).length;
  $('#favorite-count').textContent=fav.length;
  $('#favorites-section')?.classList.toggle('hidden',fav.length===0);
  $('#favorite-list').innerHTML=fav.map((j,i)=>jobCard(j,i,!!j.top_eligible)).join('');
  $('#top-list').innerHTML=top.length?top.map((j,i)=>jobCard(j,i,true)).join(''):'<div class="muted" style="padding:12px 6px">没有匹配当前搜索的 Top 岗位。</div>';
  $('#qualified-list').innerHTML=q.length?q.map((j,i)=>jobCard(j,i,false)).join(''):'<div class="muted" style="padding:12px 6px">暂无匹配的其他岗位。</div>';
  bindCards();
}
function bulletList(items,kind=''){ const arr=(items||[]).filter(Boolean); return arr.length?`<div class="bullet-list">${arr.map(x=>`<div class="bullet ${kind}">${esc(x)}</div>`).join('')}</div>`:'<div class="muted">暂无</div>'; }
const FRIENDLY_KEYS={verified:'已验证信息',official_scale_claims:'公开规模信息',confidence:'置信度',status:'状态',investors:'投资方',historical_investors_signal:'历史投资信号',note:'说明',entity:'主体',founded:'成立时间',risk:'风险',positive:'正面',negative:'负面',controversial:'争议信号',assessment:'总体判断',claims:'证据结论'};
function friendlyKey(k){return FRIENDLY_KEYS[k]||String(k).replaceAll('_',' ');}
function valueText(v){
  if(v===null||v===undefined||v==='') return '暂无';
  if(Array.isArray(v)) return v.map(x=>typeof x==='object'?Object.values(x).join(' / '):String(x)).join('；');
  if(typeof v==='object') return Object.entries(v).filter(([k])=>k!=='confidence').map(([k,x])=>`${friendlyKey(k)}：${valueText(x)}`).join('；');
  return String(v);
}
function structuredValue(v,depth=0){
  if(v===null||v===undefined||v==='') return '<span class="muted">暂无</span>';
  if(Array.isArray(v)) return `<ul class="pretty-list">${v.filter(x=>x!==null&&x!==undefined&&x!=='').map(x=>`<li>${typeof x==='object'?structuredValue(x,depth+1):esc(x)}</li>`).join('')}</ul>`;
  if(typeof v==='object'){
    const entries=Object.entries(v).filter(([,x])=>x!==null&&x!==undefined&&x!==''&&!(Array.isArray(x)&&x.length===0));
    const confidence=entries.find(([k])=>k==='confidence')?.[1];
    const body=entries.filter(([k])=>k!=='confidence').map(([k,x])=>`<div class="pretty-row"><span>${esc(friendlyKey(k))}</span><div>${structuredValue(x,depth+1)}</div></div>`).join('');
    return `${confidence?`<div class="confidence-pill">置信度 ${esc(confidence)}</div>`:''}<div class="pretty-object">${body}</div>`;
  }
  return `<span>${esc(v)}</span>`;
}
function fact(label,value){ return `<div class="fact"><label>${esc(label)}</label><div>${esc(valueText(value))}</div></div>`; }
function reportBlock(title,value){ return `<div class="report-block"><div class="report-title">${esc(title)}</div><div class="report-value structured-report">${structuredValue(value)}</div></div>`; }
function sourceItems(sources){
  if(!sources?.length)return '<div class="muted">暂无外部来源。</div>';
  return `<div class="source-list">${sources.map(s=>`<div class="source-item">
    <div class="source-head"><span class="source-type">${esc(s.source_type)}</span><span class="confidence">${esc(s.confidence)} · ${esc(s.published_at||s.retrieved_at||'')}</span></div>
    <div class="source-name">${s.url?`<a class="source-link" href="${esc(s.url)}" target="_blank" rel="noreferrer">${esc(s.source_name||s.url)}</a>`:esc(s.source_name||'来源')}</div>
    <div class="source-claims">${esc((s.claims||[]).join('；'))}${s.note?` · ${esc(s.note)}`:''}</div>
  </div>`).join('')}</div>`;
}
function reviewBox(title,items,cls){ const a=Array.isArray(items)?items:(items?[valueText(items)]:[]);return `<div class="review-box ${cls}"><h4>${esc(title)}</h4>${bulletList(a)}</div>`; }

function renderDetail(){
  const d=state.detail;if(!d)return;
  const j=d.job||{}, raw=j.raw||{}, stages=d.stages||{}, l1=stages.L1?.output||{}, l2=stages.L2?.output||{};
  const rank=d.ranking||{}, cr=d.company_report||{}, report=cr.report||{}, mat=d.material||{}, app=d.application_state||{};
  const score=rank.final_score??rank.provisional_score??stages.L2?.score??'—';
  const scoreLabel=rank.final_score!=null?'FINAL SCORE':'候选分';
  const actionState=app.executed||app.gate_status==='verified_sent'?'已投递':app.gate_status==='verification_pending'?'待验证发送':app.gate_status==='confirmed_pending_execution'||app.gate_status==='executing_initial_action'?'执行中':app.gate_status==='execution_error'?'可重试':app.gate_status==='awaiting_confirmation'?'可投递':'材料未就绪';
  const canApply=['awaiting_confirmation','execution_error'].includes(app.gate_status)&&!app.executed&&!app.confirmed;
  const needsVerify=app.gate_status==='verification_pending'&&!app.executed;
  const ui=rank.ui_state||{};
  const favorite=ui.favorite===true;
  const companySummary=report.company_summary||mat.company_situation?.summary||'L3 尚未完成，当前仅展示已验证的 BOSS 原始岗位与 L2 判断。';
  const discussion=report.public_discussion||mat.company_situation?.public_discussion||{};
  const benefits=report.benefits||mat.company_situation?.benefits||raw.benefits||[];
  const advice=mat.communication_advice||report.communication_advice||l2.questions||[];
  const greeting=mat.greeting||'';
  const resumeFocus=mat.resume_focus||l2.resume_focus||[];
  const companyFacts=[
    ['融资',raw.company_financing],['规模',raw.company_size],['行业',raw.company_industry],['招聘人',raw.recruiter_name||j.recruiter_name],['招聘人职位',raw.recruiter_title||j.recruiter_title],['活跃状态',raw.recruiter_activity],['办公地址',raw.address||j.address],['工商主体',raw.business_info?.公司名称]
  ];
  $('#detail-content').innerHTML=`
    <section class="hero">
      <div class="hero-top">
        <div>
          <div class="hero-company">${esc(j.company)}</div>
          <h2>${esc(j.title)}</h2>
          <div class="hero-sub">${esc(j.location)} · ${esc(j.salary)} · ${esc(j.work_days_per_week?j.work_days_per_week+'天/周':'工作制度待确认')} ${j.internship_months?'· '+esc(j.internship_months)+'个月':''}</div>
        </div>
        <div class="score-stack"><button class="favorite-big ${favorite?'on':''}" data-action="favorite" title="${favorite?'取消收藏':'收藏并置顶'}">★</button><div class="big-score" style="--score-deg:${Math.max(0,Math.min(100,Number(score)||0))*3.6}deg"><span>${esc(score)}</span></div><div class="score-caption">${esc(scoreLabel)}<br>${esc(actionState)}</div></div>
      </div>
      <div class="hero-signals">${(rank.highlights||[]).map(x=>`<span class="signal">${esc(x)}</span>`).join('')}</div>
      <div class="hero-actions">
        <button class="action-btn secondary" data-action="open">仅打开 BOSS</button>
        ${needsVerify?'<button class="action-btn primary" data-action="verify">重新验证发送</button>':`<button class="action-btn primary" data-action="apply" ${canApply?'':'disabled'}>${app.executed||app.gate_status==='verified_sent'?'✓ 已投递':app.gate_status==='execution_error'?'继续投递':'投递'}</button>`}
        <button class="action-btn" data-action="hold">${ui.state==='hold'?'恢复':'暂缓'}</button>
        <button class="action-btn danger" data-action="skip">跳过</button>
      </div>
    </section>

    <div class="grid-2">
      <section class="panel"><h3>为什么推荐我 <span class="panel-kicker">L2 FIT</span></h3><div class="fit-copy">${esc(l2.fit_summary||stages.L2?.summary||'暂无 L2 结论')}</div>
        <div class="metric-row">${[['L2匹配',stages.L2?.score??'—'],['公司质量',rank.company_score??'—'],['机会质量',rank.opportunity_score??'—']].map(([a,b])=>`<div class="metric"><strong>${esc(b)}</strong><span>${esc(a)}</span></div>`).join('')}</div>
        <h3 style="margin-top:18px">匹配优势</h3>${bulletList(l2.strengths,'good')}
      </section>
      <section class="panel"><h3>需要注意 <span class="panel-kicker">GAPS & RISKS</span></h3>${bulletList([...(l2.gaps||[]),...(l2.risks||[]),...(rank.constraint_state?.pending||[])],'bad')}</section>
    </div>

    <section class="detail-tabs">
      <nav class="tab-nav">
        <button class="tab-btn active" data-tab="job">岗位原文</button>
        <button class="tab-btn" data-tab="company">公司背调</button>
        <button class="tab-btn" data-tab="reviews">网络评价</button>
        <button class="tab-btn" data-tab="talk">沟通建议</button>
        <span class="tab-note">BOSS 原始事实与外部证据分层展示</span>
      </nav>

      <div class="tab-panel active" data-tab-panel="job">
        <section class="panel"><h3>BOSS 原始岗位 <span class="panel-kicker">FACT LAYER · 9227</span></h3>
          <div class="fact-grid">${companyFacts.map(([a,b])=>fact(a,b)).join('')}${fact('学历',j.education)}${fact('经验',j.experience)}${fact('标签',(j.tags||[]).join('、'))}${fact('页面地址',j.detail_url)}</div>
          <h3 style="margin-top:18px">职位描述 / 任职要求</h3><div class="jd-text">${esc(j.description||raw.description||'暂无')}</div>
          <h3 style="margin-top:18px">BOSS 公司介绍</h3><div class="jd-text" style="max-height:260px">${esc(raw.company_intro||'暂无')}</div>
        </section>
      </div>

      <div class="tab-panel" data-tab-panel="company">
        <div class="grid-2 tab-grid">
          <section class="panel"><h3>L3 公司 / 岗位背调 <span class="panel-kicker">DUE DILIGENCE</span></h3>
            ${reportBlock('公司结论',companySummary)}
            ${reportBlock('业务与产品',report.business_products)}
            ${reportBlock('融资 / 投资人',report.funding_investors)}
            ${reportBlock('经营 / 法律风险',report.operating_and_legal_risk)}
            ${reportBlock('福利',benefits)}
            ${reportBlock('工作文化',report.work_culture)}
            ${reportBlock('招聘模式',report.job_posting_pattern)}
            ${reportBlock('岗位竞争力',report.salary_competitiveness||report.job_specific_notes)}
          </section>
          <section class="panel"><h3>证据矩阵 <span class="panel-kicker">SOURCE MATRIX</span></h3>${sourceItems(cr.sources||[])}</section>
        </div>
      </div>

      <div class="tab-panel" data-tab-panel="reviews">
        <section class="panel"><h3>网络评价 <span class="panel-kicker">SIGNALS, NOT FACTS</span></h3>
          <div class="review-columns">${reviewBox('正面',discussion.positive,'positive')}${reviewBox('负面',discussion.negative,'negative')}${reviewBox('争议 / 冲突',discussion.controversial,'controversial')}${reviewBox('总体判断',discussion.assessment?[discussion.assessment]:[],'')}</div>
          <div class="report-block"><div class="report-title">证据原则</div><div class="report-value">匿名帖子只作为低置信度 signal，不转写为公司事实；冲突来源会同时保留。</div></div>
        </section>
      </div>

      <div class="tab-panel" data-tab-panel="talk">
        <div class="grid-2 tab-grid">
          <section class="panel"><h3>沟通建议 <span class="panel-kicker">TALK TRACK</span></h3>${bulletList(advice,'good')}
            ${greeting?`<h3 style="margin-top:18px">建议开场 <button id="copy-greeting" class="copy-btn">复制</button></h3><div class="greeting" id="greeting-text">${esc(greeting)}</div>`:''}
          </section>
          <section class="panel"><h3>简历重点 <span class="panel-kicker">L4 MATERIAL</span></h3>${bulletList(resumeFocus,'good')}
            <h3 style="margin-top:18px">状态链</h3><div class="fact-grid">${['L0','L1','L2','L3','L4','L5','L6'].map(k=>fact(k,stages[k]?.status||'—')).join('')}</div>
          </section>
        </div>
      </div>
    </section>`;
  bindDetailActions();
}

function bindDetailActions(){
  document.querySelectorAll('.tab-btn').forEach(btn=>btn.addEventListener('click',()=>{
    const tab=btn.dataset.tab; state.activeTab=tab;
    document.querySelectorAll('.tab-btn').forEach(x=>x.classList.toggle('active',x.dataset.tab===tab));
    document.querySelectorAll('.tab-panel').forEach(x=>x.classList.toggle('active',x.dataset.tabPanel===tab));
  }));
  document.querySelectorAll('[data-action]').forEach(btn=>btn.addEventListener('click',async()=>{
    const action=btn.dataset.action; if(!state.selectedId)return;
    try{
      btn.disabled=true;
      if(action==='favorite'){
        const favorite=state.detail?.ranking?.ui_state?.favorite===true;
        await postJSON(`/api/jobs/${state.selectedId}/favorite`,{favorite:!favorite});
        toast(favorite?'已取消收藏':'已收藏并置顶'); await refreshDashboard(false); await selectJob(state.selectedId,false); return;
      }
      if(action==='hold'){
        const held=state.detail?.ranking?.ui_state?.state==='hold';
        await postJSON(`/api/jobs/${state.selectedId}/${held?'restore':'hold'}`,{note:held?'用户恢复岗位':'用户暂缓岗位'});
        toast(held?'已恢复':'已暂缓'); await refreshDashboard(false); await selectJob(state.selectedId,false); return;
      }
      if(action==='skip'){
        await postJSON(`/api/jobs/${state.selectedId}/skip`,{note:'用户在Dashboard跳过'}); toast('已从主列表隐藏'); state.selectedId=null; await refreshDashboard(true); return;
      }
      const r=await postJSON(`/api/jobs/${state.selectedId}/${action}`);
      if(action==='open') toast('已请求在 9227 打开岗位');
      if(action==='apply'){ toast('已确认，9227 将只点击一次并到消息页验证'); setTimeout(()=>selectJob(state.selectedId,false),2500); setTimeout(()=>refreshDashboard(false),3500); }
      if(action==='verify'){ toast('正在通过 9227 消息页重新验证，不会再次点击投递'); setTimeout(()=>selectJob(state.selectedId,false),2200); setTimeout(()=>refreshDashboard(false),3200); }
    }catch(e){ toast(e.message,true); }
    finally{btn.disabled=false;}
  }));
  const copy=$('#copy-greeting'); if(copy)copy.addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('#greeting-text').textContent);toast('开场话术已复制')}catch{toast('复制失败',true)}});
}

function renderDetailError(message,id){
  $('#detail-content').innerHTML=`<div class="detail-error">
    <div class="error-icon">!</div>
    <div><h3>详情暂时无法加载</h3><p>${esc(message)}</p><p class="error-hint">列表数据不会丢失。Dashboard 服务恢复后可以直接重试，不会改变岗位状态。</p>
    <div class="error-actions"><button class="action-btn primary" id="retry-detail">重新加载详情</button><button class="action-btn" id="retry-all">重新连接服务</button></div></div>
  </div>`;
  $('#retry-detail')?.addEventListener('click',()=>selectJob(id,false));
  $('#retry-all')?.addEventListener('click',async()=>{await refreshDashboard(false);if(state.selectedId)await selectJob(state.selectedId,false);});
}
async function selectJob(id,rerenderLists=true){
  state.selectedId=id; if(rerenderLists)renderLists();
  $('#empty-state').classList.add('hidden'); $('#detail-content').classList.remove('hidden'); $('#detail-content').innerHTML='<div class="detail-loading"><div class="loading-ring"></div><div><strong>正在读取岗位详情</strong><span>完整 JD · L2 匹配 · L3 背调 · 证据矩阵</span></div></div>';
  try{state.detail=await getJSON(`/api/jobs/${encodeURIComponent(id)}`);renderDetail(); if(rerenderLists)renderLists();}
  catch(e){toast(e.message,true);renderDetailError(e.message,id);}
}
async function refreshDashboard(selectDefault=true){
  document.body.classList.add('loading');
  try{
    state.dashboard=await getJSON('/api/dashboard'); renderStatus(); renderLists();
    const all=[...(state.dashboard.today_top||[]),...(state.dashboard.qualified||[])];
    if(selectDefault && (!state.selectedId || !all.some(x=>x.job_id===state.selectedId))){ state.selectedId=all[0]?.job_id||null; if(state.selectedId)await selectJob(state.selectedId); }
    else if(state.selectedId) renderLists();
  }catch(e){toast(`加载失败：${e.message}`,true);if(state.dashboard)renderStatus();}finally{document.body.classList.remove('loading')}
}
$('#refresh-btn')?.addEventListener('click',async()=>{await refreshDashboard(false);if(state.selectedId)await selectJob(state.selectedId,false);});
$('#job-search').addEventListener('input',e=>{state.query=e.target.value.trim();renderLists();});
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();$('#job-search').focus();}});
async function heartbeat(){
  try{
    const wasOffline=state.backendOnline===false;
    await getJSON('/api/health',true);
    if(wasOffline){toast('Dashboard 服务已恢复');await refreshDashboard(false);if(state.selectedId)await selectJob(state.selectedId,false);}
  }catch{}
}

function applyTheme(theme,persist=true){
  const next=theme==='dark'?'dark':'light';
  document.documentElement.dataset.theme=next;
  if(persist)localStorage.setItem('jobAgentTheme',next);
  const icon=$('#theme-icon'), label=$('#theme-label');
  if(icon)icon.textContent=next==='light'?'☀':'☾';
  if(label)label.textContent=next==='light'?'白天':'黑夜';
}
function initTheme(){
  applyTheme(localStorage.getItem('jobAgentTheme')||'light',false);
  $('#theme-toggle')?.addEventListener('click',()=>applyTheme(document.documentElement.dataset.theme==='light'?'dark':'light'));
}
function updateProfileButtons(){
  const resume=state.profile?.profile?.resume||{};
  const uploaded=state.profile?.resume_uploaded===true||state.profile?.resume_exists===true||resume.status==='source_saved';
  const btn=$('#resume-btn'), label=$('#resume-btn-label');
  if(btn)btn.classList.toggle('ready',uploaded);
  if(label)label.textContent=uploaded?'简历已上传':'上传简历 PDF';
  if(btn&&uploaded)btn.title=`${resume.filename||'resume.pdf'} · 已保存源文件 · ${resume.uploaded_at||''}`;
}
async function loadProfileMeta(quiet=true){
  try{state.profile=await getJSON('/api/profile',quiet);updateProfileButtons();return state.profile;}catch(e){if(!quiet)toast(e.message,true);return null;}
}
async function loadPreferences(quiet=true){
  try{state.preferences=await getJSON('/api/preferences',quiet);return state.preferences;}catch(e){if(!quiet)toast(e.message,true);return null;}
}
function closeDrawer(){
  state.drawer=null;
  $('#profile-drawer')?.classList.remove('open');
  $('#profile-drawer')?.setAttribute('aria-hidden','true');
  $('#drawer-backdrop')?.classList.add('hidden');
}
function showDrawer(title,kicker,body,footer=''){
  $('#drawer-title').textContent=title; $('#drawer-kicker').textContent=kicker;
  $('#drawer-body').innerHTML=body; $('#drawer-footer').innerHTML=footer;
  $('#drawer-backdrop').classList.remove('hidden'); $('#profile-drawer').classList.add('open'); $('#profile-drawer').setAttribute('aria-hidden','false');
}
async function openResumeDrawer(){
  state.drawer='resume'; const data=await loadProfileMeta(false); if(!data)return;
  const r=data.profile?.resume||{}, uploaded=data.resume_uploaded===true||data.resume_exists===true||r.status==='source_saved';
  const agentStatus=data.resume_agent_status||r.agent_status||'not_requested';
  showDrawer('简历 PDF','RESUME',`<div class="resume-status ${uploaded?'uploaded':''}"><div class="resume-status-icon">${uploaded?'✓':'PDF'}</div><div><strong>${uploaded?esc(r.filename||'简历源文件已保存'):'还没有上传简历'}</strong><span>${uploaded?'源 PDF 已安全保存，不在前端做机械文本解析。':'上传后仅保存 PDF 原件，由本地 Agent 结合个人资料和求职要求统一处理。'}</span></div></div>${uploaded?`<div class="backup-note"><b>本地 Agent 状态：</b>${esc(agentStatus==='pending'?'待处理':agentStatus)}<br>Agent 会读取源 PDF + 补充资料 + 求职要求，完成候选人画像后再更新正式个人资料。</div>`:''}`,`<button class="drawer-btn secondary" id="drawer-upload-resume">${uploaded?'重新上传 PDF':'选择 PDF'}</button>`);
  $('#drawer-upload-resume')?.addEventListener('click',()=>$('#resume-file')?.click());
}
async function openPreferencesDrawer(){
  state.drawer='preferences'; const data=await loadPreferences(false); if(!data)return;
  showDrawer('求职要求','JOB PREFERENCES',`<div class="drawer-note">直接在这里修改即可。保存后用于后续筛选；为保护已冻结数据，历史岗位不会自动重算。</div><textarea id="profile-editor" class="profile-editor" spellcheck="false">${esc(data.raw_text||'')}</textarea>`,`<span class="footer-hint">保存前会自动备份旧版本</span><button class="drawer-btn primary" id="drawer-save-preferences">保存求职要求</button>`);
  $('#drawer-save-preferences')?.addEventListener('click',savePreferencesFromDrawer);
}
async function openSupplementDrawer(){
  state.drawer='supplement'; const data=await loadProfileMeta(false); if(!data)return;
  showDrawer('补充资料','PROFILE NOTES',`<div class="drawer-note">用于补充简历不方便表达的真实技能、项目、作品和限制。保存后会自动合并进 <b>我的资料.txt</b>。</div><textarea id="profile-editor" class="profile-editor" spellcheck="false">${esc(data.supplement_text||'')}</textarea>`,`<span class="footer-hint">自动版本备份 · 不允许覆盖简历原件</span><button class="drawer-btn primary" id="drawer-save-supplement">保存补充资料</button>`);
  $('#drawer-save-supplement')?.addEventListener('click',saveSupplementFromDrawer);
}
async function savePreferencesFromDrawer(){
  const text=$('#profile-editor')?.value||''; const btn=$('#drawer-save-preferences'); if(btn)btn.disabled=true;
  try{await postJSON('/api/preferences',{text});toast('求职要求已保存并备份');await loadPreferences();closeDrawer();}catch(e){toast(e.message,true);}finally{if(btn)btn.disabled=false;}
}
async function saveSupplementFromDrawer(){
  const text=$('#profile-editor')?.value||''; const btn=$('#drawer-save-supplement'); if(btn)btn.disabled=true;
  try{state.profile=await postJSON('/api/profile/supplement',{text});updateProfileButtons();toast('补充资料已保存，个人资料已重建');closeDrawer();}catch(e){toast(e.message,true);}finally{if(btn)btn.disabled=false;}
}
async function uploadResumeFile(file){
  if(!file)return;
  if(!file.name.toLowerCase().endsWith('.pdf')){toast('请选择 PDF 文件',true);return;}
  if(file.size>12*1024*1024){toast('PDF 不能超过 12MB',true);return;}
  const label=$('#resume-btn-label'); if(label)label.textContent='上传中…';
  try{
    const dataUrl=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=()=>reject(r.error);r.readAsDataURL(file);});
    const data_base64=String(dataUrl).split(',',2)[1]||'';
    state.profile=await requestJSON('/api/profile/resume',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filename:file.name,data_base64}),timeoutMs:30000});
    updateProfileButtons();toast('简历源文件已保存，等待本地 Agent 处理');await openResumeDrawer();
  }catch(e){toast(e.message,true);await loadProfileMeta();}finally{$('#resume-file').value='';updateProfileButtons();}
}
$('#resume-btn')?.addEventListener('click',openResumeDrawer);
$('#preferences-btn')?.addEventListener('click',openPreferencesDrawer);
$('#supplement-btn')?.addEventListener('click',openSupplementDrawer);
$('#resume-file')?.addEventListener('change',e=>uploadResumeFile(e.target.files?.[0]));
$('#drawer-close')?.addEventListener('click',closeDrawer);
$('#drawer-backdrop')?.addEventListener('click',closeDrawer);
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeDrawer();});

initTheme();
loadProfileMeta(true);
refreshDashboard(true);
setInterval(heartbeat,10000);
// Top7 是容量上限，不是等待条件；后台一旦出现新的合法候选就立即刷新到页面。
setInterval(()=>{if(!state.drawer&&!state.loading)refreshDashboard(false);},12000);
