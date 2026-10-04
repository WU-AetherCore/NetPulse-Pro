/* View controls never change network settings. Render query data as text. */
(() => {
  const $ = id => document.getElementById(id);
  const el = (tag, text, cls) => {const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls;return n;};
  const pages={dashboard:['网络总览','查看实时流量、连接状态与恢复状态'],devices:['设备管理','搜索设备，查看在线状态并管理带宽'],ranking:['流量排行','按日汇总比较设备流量，今天从零点开始'],browsing:['DNS 查询记录','域名查询来自 AdGuard Home，不代表完整网页浏览历史'],adguard:['DNS 保护','查看过滤统计，打开完整管理界面'],events:['连接记录','搜索和筛选最近 200 条设备连接事件'],about:['关于 NetPulse-Pro','网络拓扑、统计范围与使用说明']};
  for(const [id,[title,desc]] of Object.entries(pages)) {
    const head=el('div',undefined,'np-page-head'),info=el('div');info.append(el('div','NETPULSE PRO','np-kicker'),el('h2',title),el('p',desc));head.append(info);$('tab-'+id).prepend(head);
  }
  const names=['dashboard','devices','ranking','browsing','adguard','events','wifi','about'];
  const nav=[...document.querySelectorAll('.nav-tab')];nav[0].parentElement.setAttribute('role','tablist');nav[0].parentElement.setAttribute('aria-label','主导航');
  nav.forEach((b,i)=>{b.id='nav-'+names[i];b.setAttribute('role','tab');b.setAttribute('aria-controls','tab-'+names[i]);const pane=$('tab-'+names[i]);pane.setAttribute('role','tabpanel');pane.setAttribute('aria-labelledby',b.id);b.addEventListener('keydown',e=>{let j=i;if(e.key==='ArrowRight')j=(i+1)%nav.length;else if(e.key==='ArrowLeft')j=(i+nav.length-1)%nav.length;else if(e.key==='Home')j=0;else if(e.key==='End')j=nav.length-1;else return;e.preventDefault();switchTab(names[j]);nav[j].focus();});});
  const originalSwitch=window.switchTab;
  window.switchTab=function(name){originalSwitch(name);nav.forEach((b,i)=>{b.setAttribute('aria-selected',String(names[i]===name));b.tabIndex=names[i]===name?0:-1;});history.replaceState(null,'','#'+name);};
  document.querySelectorAll('#tab-devices table').forEach(t=>{t.parentElement.classList.add('np-table-wrap');t.parentElement.before(el('p','表格可左右滚动，查看全部信息与操作。','np-table-note'));});
  document.querySelectorAll('#tab-adguard>div[style*="display:grid"]').forEach(n=>n.classList.add('np-adguard-grid'));
  const tools=(parent)=>{const n=el('div',undefined,'np-tools');parent.prepend(n);return n;};
  const search=(parent,placeholder,fn)=>{const n=el('input');n.type='search';n.placeholder=placeholder;n.setAttribute('aria-label',placeholder);n.addEventListener('input',fn);parent.append(n);return n;};
  const button=(parent,text,fn)=>{const n=el('button',text,'btn');n.type='button';n.onclick=fn;parent.append(n);return n;};
  const message=(box,text)=>box.replaceChildren(el('div',text,'empty-state'));
  async function json(url){const r=await fetch(url);if(!r.ok)throw Error('请求失败（'+r.status+'）');const d=await r.json();if(d.error)throw Error(d.error);return d;}

  let events=[],page=0;
  const eventTools=tools($('eventList').parentElement);
  const eventSearch=search(eventTools,'搜索 IP 或 MAC 地址',()=>{page=0;renderEvents();});
  const eventType=el('select');eventType.setAttribute('aria-label','事件类型');[['all','全部事件'],['first_seen','首次发现'],['connect','连接'],['disconnect','离线']].forEach(([v,t])=>{const o=el('option',t);o.value=v;eventType.append(o);});eventType.onchange=()=>{page=0;renderEvents();};eventTools.append(eventType);
  button(eventTools,'刷新记录',()=>loadEvents());
  const pager=el('div',undefined,'np-pagination');$('eventList').after(pager);
  function renderEvents(){const q=eventSearch.value.toLowerCase();const filtered=events.filter(e=>((e.device_ip||'')+' '+(e.device_mac||'')).toLowerCase().includes(q)&&(eventType.value==='all'||(eventType.value==='connect'?!['disconnect','first_seen'].includes(e.event_type):e.event_type===eventType.value)));const count=Math.max(1,Math.ceil(filtered.length/50));page=Math.min(page,count-1);$('eventCount').textContent=`匹配 ${filtered.length} / 已加载 ${events.length} 条`;$('eventList').replaceChildren();filtered.slice(page*50,(page+1)*50).forEach(e=>{const row=el('div',undefined,'event-item'),info=el('div',undefined,'np-event-info');info.append(el('span',e.event_type==='first_seen'?'首次发现':e.event_type==='disconnect'?'离线':'连接','event-type event-'+e.event_type),el('span',e.device_ip||'—'),el('code',e.device_mac||'—'));row.append(info,el('time',e.time_str||'—'));$('eventList').append(row);});if(!filtered.length)message($('eventList'),'没有匹配的连接记录');pager.replaceChildren();const prev=button(pager,'上一页',()=>{page--;renderEvents();});prev.disabled=page===0;pager.append(el('span',`${page+1} / ${count}`));const next=button(pager,'下一页',()=>{page++;renderEvents();});next.disabled=page>=count-1;}
  window.loadEvents=async()=>{try{events=await json('/api/events?limit=200');renderEvents();}catch(e){message($('eventList'),'加载失败：'+e.message);}};

  let browseData=null;
  const browseTools=tools($('browsingContent').parentElement);
  const browseSearch=search(browseTools,'搜索设备、IP 或域名',renderBrowse);
  const browseButtons=[];
  [['all','全部查询'],['normal','未拦截'],['blocked','已拦截']].forEach(([v,t])=>browseButtons.push([v,button(browseTools,t,()=>{browsingFilter=v;renderBrowse();})]));
  function domainLink(domain,blocked){let n;if(!blocked&&/^(?=.{1,253}$)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}$/i.test(domain||'')){n=el('a',domain);n.href='https://'+domain;n.target='_blank';n.rel='noopener noreferrer';}else n=el('span',domain||'—');if(blocked)n.className='np-blocked';return n;}
  function renderBrowse(){if(!browseData)return;const box=$('browsingContent'),open=new Set([...box.querySelectorAll('details[open]')].map(n=>n.dataset.ip));box.replaceChildren();const q=browseSearch.value.trim().toLowerCase();browseButtons.forEach(([v,b])=>b.classList.toggle('active',v===browsingFilter));let visible=0;for(const dev of browseData.devices||[]){const found=allDevices.find(d=>d.ip===dev.ip),name=found?(found.name||found.vendor||'未知设备'):'未知设备';const deviceMatch=(name+' '+dev.ip).toLowerCase().includes(q);const accept=d=>(browsingFilter==='all'||(browsingFilter==='blocked'?!!d.blocked:!d.blocked))&&(deviceMatch||(d.domain||'').toLowerCase().includes(q));const domains=(dev.top_domains||[]).filter(accept),recent=(dev.recent||[]).filter(accept);if(!domains.length&&!recent.length)continue;visible++;const detail=el('details',undefined,'np-browse-card');detail.dataset.ip=dev.ip;detail.open=open.has(dev.ip)||!!q;const summary=el('summary');summary.append(el('strong',name+' · '+dev.ip),el('span',`查询 ${dev.total_queries} · 拦截 ${dev.blocked_count} · 匹配 ${domains.length} 个域名`));detail.append(summary);const list=el('div',undefined,'np-domain-list');list.append(el('h4','域名排行 · 最多显示 50 条'));for(const d of domains.slice(0,50)){const row=el('div',undefined,'np-domain-row');row.append(domainLink(d.domain,d.blocked),el('span',`${d.count} 次${d.blocked?' · 拦截 '+d.blocked:''}`));list.append(row);}list.append(el('h4','最近查询 · 最多显示 20 条'));for(const d of recent.slice(0,20)){const row=el('div',undefined,'np-domain-row');row.append(domainLink(d.domain,d.blocked),el('time',(d.time||'').replace('T',' ').slice(0,19)));list.append(row);}detail.append(list);box.append(detail);}if(!visible)message(box,'没有匹配的 DNS 查询记录');$('browsingInfo').textContent=`显示 ${visible} 台 / 共 ${browseData.total_devices||0} 台 · ${browseData.total_queries||0} 条查询（已加载）`;}
  window.loadBrowsingHistory=async()=>{try{browseData=await json('/api/browsing-history?limit=5000');renderBrowse();}catch(e){message($('browsingContent'),'加载失败：'+e.message);}};
  window.setBrowsingFilter=v=>{browsingFilter=v;renderBrowse();};

  let ranking=[],showZeros=false;
  const rankTools=tools($('rankingList').parentElement);
  const rankSearch=search(rankTools,'搜索排行设备或 IP',renderRanks);
  const zeroLabel=el('label',undefined,'np-check'),zero=el('input');zero.type='checkbox';zero.onchange=()=>{showZeros=zero.checked;renderRanks();};zeroLabel.append(zero,document.createTextNode(' 显示零流量设备'));rankTools.append(zeroLabel);
  const rankCount=el('span','','np-count');rankTools.append(rankCount);
  function renderRanks(){const q=rankSearch.value.toLowerCase(),rows=ranking.filter(d=>(showZeros||Number(d.total_upload)+Number(d.total_download)>0)&&((d.name||d.vendor||'')+' '+(d.ip||'')).toLowerCase().includes(q));const max=Math.max(1,...rows.map(d=>Number(d.total_upload)+Number(d.total_download)));rankCount.textContent=`显示 ${rows.length} / ${ranking.length} 台`;$('rankingList').replaceChildren();rows.forEach((d,i)=>{const row=el('button',undefined,'np-rank-row');row.type='button';row.onclick=()=>showDeviceDetail(d.mac);const info=el('div',undefined,'np-rank-info');info.append(el('strong',(d.name||d.vendor||'未知设备')+' · '+(d.ip||'—')),el('small',`${d.is_online?'在线':'离线'} · ↑ ${d.total_upload_str} · ↓ ${d.total_download_str}`));const bar=el('div',undefined,'np-rank-bar'),fill=el('span');fill.style.width=(100*(Number(d.total_upload)+Number(d.total_download))/max)+'%';bar.append(fill);info.append(bar);row.append(el('span',String(i+1),'np-rank-number'),info,el('strong',d.total_str||'0 B'));$('rankingList').append(row);});if(!rows.length)message($('rankingList'),'当前筛选范围内暂无流量，或启用“显示零流量设备”');}
  window.loadRanking=async()=>{try{const data=await json(`/api/traffic-ranking?days=${rankingDays}&sort=${getCustomSelectValue('rankingSort')}`);ranking=data.ranking||[];renderRanks();}catch(e){message($('rankingList'),'加载失败：'+e.message);}};
  const originalNetwork=window.refreshNetworkSettings;
  window.refreshNetworkSettings=async()=>{await originalNetwork();try{const h=await json('/api/network/health');if(h.wifi?.SSID)$('networkSsid').textContent=h.wifi.SSID;if($('networkLinkSpeed'))$('networkLinkSpeed').textContent=h.ethernet?.speed?`${h.ethernet.speed} Mbps · ${h.ethernet.duplex==='full'?'全双工':h.ethernet.duplex}`:'未读取到链路速率';}catch(e){if($('networkLinkSpeed'))$('networkLinkSpeed').textContent='链路状态暂不可用';}};
  const initial=location.hash.slice(1);switchTab(names.includes(initial)?initial:'dashboard');
})();
