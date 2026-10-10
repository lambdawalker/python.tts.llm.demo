'use strict';
const $ = id => document.getElementById(id);
let state = null, current = sessionStorage.getItem('chat'), busy = false, polling = false;
let messageKey = '', reviewKey = '', assetsKey = '', voicesKey = '', playbackSeen = null;
let draft = null;
const el = (tag, text, cls) => { const n = document.createElement(tag); if(text !== undefined) n.textContent=text; if(cls)n.className=cls; return n; };
function error(e){ $('error').textContent=e.message || String(e); $('error').hidden=false; }
async function api(path, body, method='POST') {
  const response = await fetch(path, {method, headers:{'Content-Type':'application/json','X-Demo-Request':'1'}, body:body === undefined ? undefined : JSON.stringify(body)});
  if(!response.ok){const data=await response.json(); throw new Error(typeof data.detail==='string'?data.detail:'Check the values and try again.');}
  return response.json();
}
async function mutate(work){ if(busy)return; busy=true; $('error').hidden=true; try {await work(); await refresh();} catch(e){error(e);} finally {busy=false;} }
function switchTab(tab){document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab)); ['chat','assets','voices'].forEach(t=>$(t+'-panel').hidden=t!==tab);}
document.querySelectorAll('.tab').forEach(b=>b.addEventListener('click',()=>switchTab(b.dataset.tab)));
function selectChat(id){current=id;sessionStorage.setItem('chat',id);messageKey='';reviewKey='';render();switchTab('chat');}
$('reset-session').onclick=()=>mutate(async()=>{await api('/api/tts/reset-session');$('error').textContent='TTS session reset. Your next task will use a fresh session; local assets and voices are retained.';$('error').hidden=false;});
$('new-chat').onclick=()=>mutate(async()=>{const c=await api('/api/chats');current=c.id;sessionStorage.setItem('chat',current);messageKey='';reviewKey='';switchTab('chat');});
$('composer').onsubmit=e=>{e.preventDefault();const text=$('message').value.trim();if(!text)return;mutate(async()=>{await api(`/api/chats/${current}/messages`,{text});$('message').value='';});};
$('message').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('composer').requestSubmit();}});
document.querySelectorAll('[data-prompt]').forEach(b=>b.onclick=()=>{$('message').value=b.dataset.prompt;$('message').focus();});
function activeRun(){return state?.runs.find(r=>['running','cancelling'].includes(r.status));}
$('cancel').onclick=()=>mutate(async()=>{const run=activeRun();if(run)await api(`/api/runs/${run.id}/cancel`);});
async function action(action, fields={}){await api(`/api/chats/${current}/actions`,{action,...fields});}
function button(text, handler, cls){const b=el('button',text,cls);b.type='button';b.onclick=handler;return b;}
function play(asset){$('player-area').hidden=false;$('player-title').textContent=asset.title;$('player').src=`/api/assets/${asset.id}/audio`;$('play-hint').textContent='';$('player').play().catch(()=>{$('play-hint').textContent='Click Play in the audio player to start.';});}
function renderReview(chat){
 const key=JSON.stringify([chat?.pending,chat?.last_plan]);if(key===reviewKey)return;reviewKey=key;const box=$('review');box.replaceChildren();box.hidden=!chat?.pending&&!chat?.last_plan;if(!chat?.pending){if(chat?.last_plan)box.append(button('Revisit last plan',()=>mutate(()=>action('restore'))));return;}
 draft=structuredClone(chat.pending);box.append(el('h3',draft.title),el('p','Would you like to review first? Edit below, or choose Generate now.'));
 const detail=el('details');detail.append(el('summary',draft.kind==='speech'?'Review script':'Review voice description'));
 if(draft.kind==='voice_design'){
  for(const [field,label] of [['description','Voice description'],['preview_text','Preview words']]){const l=el('label',label);const t=el('textarea');t.value=draft[field];t.rows=3;t.oninput=()=>draft[field]=t.value;l.append(t);detail.append(l);}
 }else{draft.turns.forEach((turn,i)=>{const speaker=draft.speakers.find(s=>s.name===turn.speaker);const label=el('label',`${i+1}. ${turn.speaker} · ${speaker.model}`);const text=el('textarea');text.value=turn.text;text.rows=2;text.oninput=()=>turn.text=text.value;label.append(text);detail.append(label);});}
 box.append(detail);const controls=el('div',undefined,'actions');controls.append(button('Generate now',()=>mutate(async()=>{await api(`/api/chats/${current}/plan`,draft,'PUT');await action('generate');}),'primary'),button('Save edits',()=>mutate(async()=>{await api(`/api/chats/${current}/plan`,draft,'PUT');})),button('Discard',()=>mutate(()=>action('discard'))));box.append(controls);
}
function renderAssets(){
 const key=JSON.stringify(state.assets);if(key===assetsKey)return;assetsKey=key;const target=$('assets');target.replaceChildren();
 if(!state.assets.length){target.append(el('div','Your generated audio will appear here.', 'empty'));return;}
 [...state.assets].reverse().forEach(asset=>{const card=el('article',undefined,'card');card.append(el('span',asset.kind.replaceAll('_',' '),'kind'),el('h3',asset.title),el('p',`${asset.duration.toFixed(1)} seconds · ${(asset.sample_rate/1000).toFixed(0)} kHz · WAV`));const actions=el('div',undefined,'actions');actions.append(button('▶ Play',()=>play(asset)));const download=el('a','Download');download.href=`/api/assets/${asset.id}/audio?download=true`;actions.append(download);card.append(actions);
 if(asset.kind==='voice_preview'){
  if(asset.reusable_models?.length){const details=el('details');details.append(el('summary','Save as a reusable voice'));const form=el('form');const label=el('label','Voice name');const name=el('input');name.placeholder='e.g. Monica';name.required=true;name.maxLength=80;label.append(name);const modelLabel=el('label','Synthesis model');const model=el('select');asset.reusable_models.forEach(m=>{const o=el('option',m);o.value=m;model.append(o);});modelLabel.append(model);const save=el('button','Save voice','primary');save.type='submit';form.append(label,modelLabel,save);form.onsubmit=e=>{e.preventDefault();mutate(()=>action('save',{asset_id:asset.id,name:name.value,model:model.value}));};details.append(form);card.append(details);
  }else card.append(el('p','Preview only. The current service has no compatible voice-reuse model.'));
 }
 target.append(card);});
}
function renderVoices(){const key=JSON.stringify(state.voices);if(key===voicesKey)return;voicesKey=key;const target=$('voices');target.replaceChildren();if(!state.voices.length){target.append(el('div','Save a designed voice from Assets to use it again.', 'empty'));return;}state.voices.forEach(v=>{const card=el('article',undefined,'card');card.append(el('span','SAVED REFERENCE','kind'),el('h3',v.name),el('p',v.description),el('p',v.model));const asset=state.assets.find(a=>a.id===v.asset_id);if(asset)card.append(button('▶ Preview',()=>play(asset)));card.append(button('Use in chat',()=>{switchTab('chat');$('message').value=`Use my saved voice ${v.name} to say: `;$('message').focus();}));target.append(card);});}
function render(){
 if(!state)return;if(!state.chats.some(c=>c.id===current))current=state.chats[0]?.id;
 const list=$('chats');list.replaceChildren();state.chats.forEach(c=>list.append(button(c.title,()=>selectChat(c.id),c.id===current?'selected':'')));
 const messages=state.messages.filter(m=>m.chat_id===current),key=JSON.stringify(messages);if(key!==messageKey){messageKey=key;$('messages').replaceChildren();messages.forEach(m=>{const node=el('div',undefined,`message ${m.role}`);if(m.role!=='status')node.append(el('small',m.role==='user'?'YOU':'AUDIO ASSISTANT'));node.append(document.createTextNode(m.content));$('messages').append(node);});}
 $('welcome').hidden=messages.length>0;renderReview(state.chats.find(c=>c.id===current));
 const run=activeRun();$('progress').hidden=!run;$('send').disabled=!!run;$('new-chat').disabled=!!run;$('reset-session').disabled=!!run;
 $('review').querySelectorAll('button,textarea').forEach(b=>b.disabled=!!run);
 if(run){$('stage').textContent=run.stage;$('counts').textContent=run.total?`${run.completed} / ${run.total} turns complete`:'';$('cancel').disabled=run.status==='cancelling';}
 $('asset-count').textContent=state.assets.length;$('voice-count').textContent=state.voices.length;renderAssets();renderVoices();
 if(playbackSeen===null)playbackSeen=new Set(state.playback.map(p=>p.id));else for(const event of state.playback){if(!playbackSeen.has(event.id)){playbackSeen.add(event.id);if(event.chat_id===current){const asset=state.assets.find(a=>a.id===event.asset_id);if(asset)play(asset);}}}
}
async function refresh(){const response=await fetch('/api/state');if(!response.ok)throw new Error('Cannot read the workspace.');state=await response.json();render();}
async function boot(){try{await refresh();if(!state.chats.length){const chat=await api('/api/chats');current=chat.id;await refresh();}}catch(e){error(e);}setInterval(async()=>{if(polling)return;polling=true;try{await refresh();}catch(e){error(e);}finally{polling=false;}},1000);}
boot();
