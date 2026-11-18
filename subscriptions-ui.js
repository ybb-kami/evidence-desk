/* UI for explicitly enabled local arXiv category checks. */
(function(){
  'use strict';
  const panel=document.getElementById('subscriptions-panel');
  if(!panel) return;
  const make=(tag,text,attrs={})=>{
    const node=document.createElement(tag);
    if(text!==undefined) node.textContent=text;
    Object.entries(attrs).forEach(([key,value])=>node.setAttribute(key,value));
    return node;
  };
  panel.setAttribute('aria-labelledby','subscriptions-title');
  const title=make('h2','arXiv 分类订阅',{id:'subscriptions-title'});
  const note=make('p','默认关闭。明确启用后，仅在本机程序运行时按周期发送所选公开分类，不发送文库或反馈，不下载论文。第一次成功刷新只建立基线，每次最多查看 12 条公开记录，不保证覆盖各期。',{class:'subtle'});
  const list=make('fieldset');list.append(make('legend','允许的 arXiv 分类'));
  const interval=make('select',undefined,{id:'subscription-hours'});
  const clock=make('label','刷新周期：',{for:'subscription-hours'});clock.append(interval);
  const consent=make('input',undefined,{type:'checkbox',id:'subscription-consent'});
  const check=make('label',undefined,{for:'subscription-consent'});
  check.append(consent,make('span','我明确同意按此周期向 arXiv 发送所选公开分类。'));
  const buttons=make('div',undefined,{class:'actions'});
  const start=make('button','启用 / 更新',{type:'button'}),stop=make('button','关闭订阅',{type:'button'}),
        refresh=make('button','查看当前状态',{type:'button'}),clear=make('button','清空本机未读',{type:'button'});
  buttons.append(start,stop,refresh,clear);
  const status=make('p','正在读取本机设置…',{role:'status',class:'subtle'});
  const schedule=make('ul'), results=make('ul',undefined,{class:'card-list'});
  panel.append(title,note,list,clock,check,buttons,status,schedule,results);
  let state, busy=false, options=[], lastPoll;
  const safeCategory=/^(cs\.(AI|CL|CV|LG)|stat\.ML|math\.(CO|OC))$/;
  const safeId=/^(?:\d{4}\.\d{4,5}|[a-z][a-z.-]{0,30}\/\d{7})$/;
  const errors={busy:'本机已有请求',cooldown:'请求冷却',rate_limited:'arXiv 限流',timeout:'网络超时',
                http_error:'arXiv 返回错误',redirect_refused:'拒绝跳转',invalid_response:'题录无效',network_error:'网络不可用'};
  const api=async(path,payload)=>{
    const reply=await fetch(path,payload===undefined?{cache:'no-store'}:{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)
    });
    const body=await reply.json();
    if(!reply.ok)throw Error(typeof body.error==='string'?body.error:'本机请求失败。');
    return body;
  };
  function controls(){
    start.disabled=busy||!state||!consent.checked||!options.some(option=>option.checked);
    stop.disabled=busy||!state?.enabled;
    clear.disabled=busy||!state?.inbox?.length;
    refresh.disabled=busy;
  }
  function render(next,reset=false){
    state=next;
    if(reset){
      options=next.available_categories.filter(c=>safeCategory.test(c)).map(category=>{
        const opt=make('input',undefined,{type:'checkbox',value:category});
        opt.checked=next.categories.includes(category);
        opt.onchange=controls;
        return opt;
      });
      list.replaceChildren(make('legend','允许的 arXiv 分类'),...options.map(opt=>{
        const label=make('label');
        label.append(opt,make('span',opt.value));
        return label;
      }));
      interval.replaceChildren(...next.allowed_intervals.filter(h=>[6,12,24].includes(h))
                                       .map(h=>make('option',h+' 小时',{value:String(h)})));
      interval.value=String(next.interval_hours);
    }
    status.textContent=(next.source_mode==='simulation'?'模拟模式，不连接真实 arXiv。':'')+
      (next.enabled?(next.busy?'订阅已启用，正在刷新公开题录。':'订阅已启用。'):'订阅已关闭。')+
      ' 本机未读 '+next.inbox.length+' 条。';
    schedule.replaceChildren(...Object.entries(next.schedule).filter(([cat])=>safeCategory.test(cat)).map(([cat,rec])=>{
      const date=Number.isFinite(rec.due)?new Date(rec.due*1000).toLocaleString():'未知时间';
      return make('li',cat+'：'+(next.enabled?'预计检查 '+date+'；':'')+
        (!rec.baseline?'尚未建立基线。':'已建立基线。')+(rec.error?' 最近状态：'+(errors[rec.error]||'不可用')+'。':''));
    }));
    results.replaceChildren(...next.inbox.filter(item=>safeId.test(item.id)&&safeCategory.test(item.category)).map(item=>{
      const row=make('li',undefined,{class:'evidence'});
      row.append(make('a',item.title,{href:'https://arxiv.org/abs/'+item.id,target:'_blank',rel:'noopener noreferrer'}),
                 make('p',item.category+' · '+item.published.slice(0,10)+' · 公开书目线索，未核查全文。',{class:'subtle'}));
      return row;
    }));
    controls();
  }
  async function load(){
    if(busy) return;
    busy=true;controls();
    try {render(await api('/api/subscriptions'),!state);}
    catch(error){status.textContent='无法读取订阅：'+error.message;}
    finally {busy=false;controls();}
  }
  async function update(path,payload){
    if(busy)return;
    busy=true;controls();
    try{render(await api(path,payload),path==='/api/subscriptions');}
    catch(error){status.textContent='订阅设置未更新：'+error.message;}
    finally{busy=false;controls();}
  }
  start.onclick=()=>{
    if(start.disabled||!consent.checked)return;
    consent.checked=false;
    update('/api/subscriptions',{enabled:true,categories:options.filter(o=>o.checked).map(o=>o.value),
                                interval_hours:Number(interval.value),consent:true});
  };
  stop.onclick=()=>{if(!stop.disabled)update('/api/subscriptions',{enabled:false});};
  clear.onclick=()=>{if(!clear.disabled)update('/api/subscriptions/read',{});};
  refresh.onclick=load;
  consent.onchange=controls;
  function poll(){
    clearTimeout(lastPoll);
    if(!document.hidden)lastPoll=setTimeout(async()=>{if(state?.enabled) await load();poll();},30000);
  }
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)load();poll();});
  controls();load();poll();
})();
