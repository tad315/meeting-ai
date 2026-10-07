const $ = s => document.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let current = null, meetings = [], caps = {}, mode = 'live', recording = null, polling = false;
const time = value => { const n=Math.max(0, Math.floor(value || 0)); return `${Math.floor(n/60).toString().padStart(2,'0')}:${(n%60).toString().padStart(2,'0')}`; };
const busyStatus = item => ['recording','processing'].includes(item.status);
const colour = speaker => ['#e7edd5','#e1ebee','#eee3d9','#e9e1ed','#f0edce'][Number(speaker || 0)%5];
function toast(message) { $('#toast').textContent = message; $('#toast').hidden=false; clearTimeout(toast.timer); toast.timer=setTimeout(()=>$('#toast').hidden=true,8000); }
async function api(path, options={}) {
  const response=await fetch('/api'+path, options);
  const data=await response.json();
  if (!response.ok) throw new Error(typeof data.detail==='string'?data.detail:'The request could not be completed.');
  return data;
}
const json = (method, body) => ({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
function cited(text) { return esc(text).replace(/\[(s\d+)\]/g, '<button class="citation" data-cite="$1">↗ $1</button>'); }
async function refreshList() { meetings=await api('/meetings'); renderList(); }
function renderList() {
  const filter=$('#search').value.toLowerCase();
  $('#count').textContent=meetings.length; $('#meeting-total').textContent=meetings.length;
  const shown=meetings.filter(m=>m.title.toLowerCase().includes(filter));
  $('#meetings').innerHTML=shown.map(m=>`<button class="meeting-row" data-meeting="${esc(m.id)}"><span class="meeting-icon">${m.source==='live'?'◉':'≋'}</span><span><span class="row-title">${esc(m.title)}</span><div class="row-meta">${esc(new Date(m.created_at).toLocaleDateString('en',{month:'short',day:'numeric',year:'numeric'}))} &nbsp; · &nbsp; ${time(m.duration)} &nbsp; · &nbsp; ${m.source==='live'?'Live meeting':'Imported audio'}</div></span><span class="row-right">${Object.keys(m.speakers).length} voices</span><span class="badge">${esc(m.status)}</span><span>↗</span></button>`).join('') || `<div class="empty"><div style="font-size:30px">≋</div><h3>${filter?'No matching conversations':'Your next great conversation starts here.'}</h3><p>${filter?'Try another title.':'Record a meeting or bring an audio file. Your notes will find their home here.'}</p></div>`;
}
async function openMeeting(id) {
  current=await api('/meetings/'+id); $('#dashboard').hidden=true; $('#detail').hidden=false;
  $('#crumb').textContent=current.title; renderMeeting(); setTab('transcript');
}
function home() {
  if (recording) { toast('End the current recording before leaving this meeting.'); return; }
  current=null; $('#dashboard').hidden=false; $('#detail').hidden=true; $('#crumb').textContent='Meetings'; refreshList().catch(e=>toast(e.message));
}
function row(segment, interim=false) {
  const name=current.speakers[segment.speaker] || (segment.speaker==null?'Unassigned':`Speaker ${Number(segment.speaker)+1}`);
  return `<article class="transcript-row" id="segment-${esc(segment.id || '')}"><button class="timestamp" data-time="${Number(segment.start)}" aria-label="Play at ${time(segment.start)}">${time(segment.start)}</button><div><span class="speaker-name" style="background:${colour(segment.speaker)}">${esc(name)}</span>${segment.provisional||interim?'<span class="provisional">Provisional</span>':''}${!interim&&!busyStatus(current)?`<button class="segment-tools" data-edit="${esc(segment.id)}">Edit text</button>`:''}<p>${esc(segment.text)}</p></div></article>`;
}
function renderMeeting() {
  if (!current) return;
  $('#meeting-title').textContent=current.title;
  $('#meeting-meta').textContent=`${current.source==='live'?'LIVE MEETING':'IMPORTED AUDIO'}  /  ENGLISH  /  ${current.provider==='local'?'LOCAL PROCESSING':'API PROCESSING'}`;
  $('#export').href=`/api/meetings/${current.id}/export`;
  $('#meeting-status').textContent=current.status==='processing'?'Processing your audio…':current.status==='ready'?'Transcript ready · '+time(current.duration):'';
  $('#error-panel').hidden=!current.error&&!current.auto_summary_error;
  $('#error-panel').innerHTML=esc(current.error || (current.auto_summary_error?`Transcript saved. Automatic summary: ${current.auto_summary_error}`:''))+(current.error&&!busyStatus(current)?'<button id="retry">Retry saved audio</button>':'');
  const audioUrl=`/api/meetings/${current.id}/audio`;
  $('.audio-container').hidden=!current.audio||busyStatus(current);
  if (current.audio && $('#audio').getAttribute('src')!==audioUrl) $('#audio').src=audioUrl;
  $('#word-count').textContent=`${current.segments.reduce((n,s)=>n+s.text.trim().split(/\s+/).length,0)} words`;
  $('#speakers').innerHTML=Object.entries(current.speakers).map(([id,name])=>`<button class="speaker-chip" data-speaker="${esc(id)}" ${busyStatus(current)?'disabled':''}>● ${esc(name)} ${busyStatus(current)?'':'↗'}</button>`).join('');
  $('#transcript').innerHTML=current.segments.map(s=>row(s)).join('') || `<div class="empty"><h3>${busyStatus(current)?'Listening for the conversation…':'No transcript yet.'}</h3><p>${busyStatus(current)?'Words and speaker labels will appear here as audio is processed.':'Process a recording to see timestamped speaker turns.'}</p></div>`;
  $('#summary-output').innerHTML=current.summary?cited(current.summary.text):'<span class="muted">Your meeting summary will appear here. Every decision should lead back to the transcript.</span>';
  $('#chat-messages').innerHTML=current.messages.map(m=>`<div class="message ${m.role==='user'?'user':'assistant'}">${cited(m.text)}</div>`).join('');
  $('#generate-summary').disabled=busyStatus(current)||!current.segments.length;
  $('#send-question').disabled=busyStatus(current)||!current.segments.length;
}
function setTab(tab) {
  document.querySelectorAll('[data-tab]').forEach(button=>button.classList.toggle('selected',button.dataset.tab===tab));
  ['transcript','summary','chat'].forEach(t=>$(`#${t}-tab`).hidden=t!==tab);
}
function providerNotes() {
  const cloud=$('#provider').value==='deepgram';
  $('#provider-note').textContent=cloud?'Streaming speaker labels with interim transcripts. Requires a configured Deepgram API key. Live sessions: up to 60 minutes.':'Processes on this computer. Speaker labels update after each cumulative analysis (at least 8 seconds, slower on CPU). Local live sessions: up to 10 minutes. Labels may change as more speech arrives.';
  $('#privacy-note').textContent=cloud?'Audio will be sent to Deepgram. Selecting an API summary also sends the transcript to your configured language-model provider.':$('#auto-summary').value==='api'?'Audio is processed locally. The transcript will be sent to your configured language-model API for summarization.':'Audio and transcript processing stay local. First use may download model files.';
}
function showCreate(type) {
  mode=type; $('#create-heading').textContent=type==='live'?'Start a live meeting':'Import a recording';
  $('#create-submit').textContent=type==='live'?'Start meeting →':'Import & transcribe →';
  $('#file-field').hidden=type!=='upload'; $('#capture-field').hidden=type!=='live';
  $('#file').required=type==='upload'; $('#create-error').textContent='';
  $('#title').value=''; providerNotes(); $('#create-dialog').showModal();
}
async function showSettings() {
  caps=await api('/health');
  $('#connections').innerHTML=[['Local speech models',caps.local_installed?'Installed':'Not installed'],['pyannote model access',caps.hf_configured?'Token configured':'HF_TOKEN needed'],['Deepgram speech API',caps.deepgram_configured?'Configured':'Key needed'],['Local summary model',caps.ollama_model+' · Ollama required'],['Language-model API',caps.llm_api_configured?'Configured':'Not configured'],['Audio decoder',caps.ffmpeg_available?'Available':'FFmpeg needed']].map(([a,b])=>`<div class="connection"><b>${esc(a)}</b><span>${esc(b)}</span></div>`).join('');
  $('#settings-dialog').showModal();
}
async function startCapture() {
  let streams=[], context=null;
  try {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('Open this app on localhost in a browser with microphone support.');
    if ($('#capture').value==='screen') {
      const screen=await navigator.mediaDevices.getDisplayMedia({video:true,audio:true}); streams.push(screen);
      if (!screen.getAudioTracks().length) throw new Error('No shared audio. Select a meeting tab and enable Share tab audio.');
    }
    streams.push(await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true},video:false}));
    context=new AudioContext({sampleRate:16000});
    if (context.sampleRate!==16000) throw new Error('This browser does not support 16 kHz recording. Try Chrome or Edge.');
    await context.audioWorklet.addModule('/pcm-worklet.js');
    await context.resume();
    const node=new AudioWorkletNode(context,'pcm-recorder');
    for (const stream of streams) { const source=context.createMediaStreamSource(stream); const gain=context.createGain(); gain.gain.value=streams.length>1?.5:1; source.connect(gain).connect(node); }
    const muted=context.createGain(); muted.gain.value=0; node.connect(muted).connect(context.destination);
    return {streams,context,node};
  } catch(e) { streams.forEach(s=>s.getTracks().forEach(t=>t.stop())); if(context) await context.close(); throw e; }
}
async function cleanupCapture() {
  if (!recording) return;
  recording.node.port.onmessage=null;
  recording.streams.forEach(s=>s.getTracks().forEach(t=>t.stop()));
  if (recording.context.state!=='closed') await recording.context.close();
  clearInterval(recording.timer);
}
async function startLive(capture, item) {
  const socket=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/api/live/${item.id}`);
  recording={...capture,socket,started:Date.now(),stopping:false,samples:0};
  $('#recording-bar').hidden=false; $('#stop').disabled=false; $('#elapsed').textContent='00:00';
  $('#live-message').textContent='Connecting to speech recognition…';
  socket.onopen=()=>socket.send(JSON.stringify({sample_rate:16000,llm:$('#auto-summary').value}));
  socket.onmessage=event=>{
    const data=JSON.parse(event.data);
    if (data.type==='ready') {
      $('#live-message').textContent='Listening · speaker labels update as people speak';
      capture.node.port.onmessage=({data:pcm})=>{
        if (socket.readyState!==WebSocket.OPEN||!recording||recording.stopping) return;
        if (socket.bufferedAmount>1024*1024) { toast('The connection cannot keep up. Recording stopped; retry the saved audio.'); stopLive(); return; }
        socket.send(pcm); recording.samples+=pcm.byteLength/2;
        $('#elapsed').textContent=time(recording.samples/16000);
      };
    }
    if(data.type==='status') $('#live-message').textContent=data.message;
    if(data.type==='interim') $('#interim').innerHTML=data.segments.map(s=>row(s,true)).join('');
    if(data.type==='transcript'||data.type==='complete') { current=data.meeting; renderMeeting(); $('#interim').innerHTML=''; }
    if(data.type==='complete') { toast('Meeting saved. Your transcript is ready.'); finishLive(); }
    if(data.type==='error') { toast(data.message); finishLive(); }
  };
  socket.onerror=()=>toast('The recording connection failed. Check the backend and AI connections.');
  socket.onclose=()=>{ if(recording){ const id=current?.id; finishLive().then(()=>id&&openMeeting(id)); } };
  for (const stream of capture.streams) for (const track of stream.getAudioTracks()) track.onended=()=>{ if(recording&&!recording.stopping){toast('The audio source ended. Finishing this meeting.');stopLive();} };
}
async function stopLive() {
  if(!recording||recording.stopping) return;
  recording.stopping=true; $('#stop').disabled=true; $('#live-message').textContent='Saving the last words…';
  await cleanupCapture();
  if(recording?.socket.readyState===WebSocket.OPEN) recording.socket.send(JSON.stringify({type:'stop'}));
  else await finishLive();
}
async function finishLive() {
  await cleanupCapture();
  recording=null; $('#recording-bar').hidden=true; $('#interim').innerHTML='';
  await refreshList();
}
$('#create-form').onsubmit=async event=>{
  event.preventDefault(); $('#create-submit').disabled=true; $('#create-error').textContent='';
  let capture=null;
  try {
    // Provider check before requesting microphone access or creating a meeting.
    caps=await api('/health');
    const provider=$('#provider').value;
    if(provider==='deepgram'&&!caps.deepgram_configured) throw new Error('Configure DEEPGRAM_API_KEY in meeting-ai/.env and restart the server first.');
    if(provider==='local'&&(!caps.local_installed||!caps.hf_configured)) throw new Error('Local mode needs requirements-local.txt and HF_TOKEN. See AI connections and README.md.');
    if(mode==='upload') {
      const file=$('#file').files[0]; if(!file) throw new Error('Choose an audio file.'); if(file.size>250*1024*1024) throw new Error('The file must be smaller than 250 MB.');
      const body=new FormData(); body.append('file',file); body.append('title',$('#title').value);body.append('provider',provider);body.append('llm',$('#auto-summary').value);
      $('#create-submit').textContent='Uploading…'; const item=await api('/upload',{method:'POST',body}); $('#create-dialog').close(); await openMeeting(item.id); await refreshList();
    } else {
      capture=await startCapture(); const item=await api('/meetings',json('POST',{title:$('#title').value,provider}));
      $('#create-dialog').close(); await openMeeting(item.id); await startLive(capture,item); capture=null;
    }
  } catch(e) { $('#create-error').textContent=e.message; if(capture){capture.streams.forEach(s=>s.getTracks().forEach(t=>t.stop()));await capture.context.close();} }
  finally { $('#create-submit').disabled=false; $('#create-submit').textContent=mode==='live'?'Start meeting →':'Import & transcribe →'; }
};
$('#generate-summary').onclick=async()=>{
  if(!current)return; const id=current.id; $('#generate-summary').disabled=true; $('#generate-summary').textContent='Reading the conversation…';
  try { const summary=await api(`/meetings/${id}/summary`,json('POST',{provider:$('#llm').value})); if(current?.id===id){current.summary=summary;renderMeeting();} }
  catch(e){toast(e.message);} finally { $('#generate-summary').textContent='Generate summary'; if(current)$('#generate-summary').disabled=busyStatus(current)||!current.segments.length; }
};
$('#chat-form').onsubmit=async event=>{
  event.preventDefault(); if(!current)return; const id=current.id, question=$('#question').value.trim(); if(!question)return;
  $('#send-question').disabled=true; $('#send-question').textContent='Thinking…';
  try{await api(`/meetings/${id}/chat`,json('POST',{provider:$('#llm').value,question}));if(current?.id===id){current=await api('/meetings/'+id);renderMeeting();$('#question').value='';}}
  catch(e){toast(e.message);}finally{$('#send-question').textContent='Ask ↗';if(current)$('#send-question').disabled=busyStatus(current)||!current.segments.length;}
};
document.addEventListener('click',async event=>{
  const el=event.target.closest('button,a'); if(!el)return;
  try{
    if(el.dataset.close) $('#'+el.dataset.close).close();
    if(el.dataset.meeting) await openMeeting(el.dataset.meeting);
    if(el.dataset.tab) setTab(el.dataset.tab);
    if(el.dataset.time){if(current.audio&&!busyStatus(current)){$('#audio').currentTime=Number(el.dataset.time);await $('#audio').play();}}
    if(el.dataset.cite){setTab('transcript');const row=$('#segment-'+el.dataset.cite);if(row){row.scrollIntoView({behavior:'smooth',block:'center'});row.classList.add('highlight');setTimeout(()=>row.classList.remove('highlight'),2500);}}
    if(el.dataset.speaker){const name=prompt('Name this speaker:',current.speakers[el.dataset.speaker]);if(name?.trim()){current=await api(`/meetings/${current.id}/speakers/${el.dataset.speaker}`,json('PATCH',{name}));renderMeeting();}}
    if(el.dataset.edit){const segment=current.segments.find(s=>s.id===el.dataset.edit);const text=prompt('Correct the transcript. The original text is preserved.',segment.text);if(text?.trim()){current=await api(`/meetings/${current.id}/segments/${segment.id}`,json('PATCH',{text}));renderMeeting();}}
    if(el.id==='retry'){await api(`/meetings/${current.id}/retry`,{method:'POST'});current=await api('/meetings/'+current.id);renderMeeting();}
  }catch(e){toast(e.message);}
});
$('#home').onclick=home;$('#back').onclick=home;$('#new-live').onclick=()=>showCreate('live');$('#new-upload').onclick=()=>showCreate('upload');
$('#settings').onclick=()=>showSettings().catch(e=>toast(e.message));$('#settings-top').onclick=$('#settings').onclick;
$('#search').oninput=renderList;$('#provider').onchange=providerNotes;$('#auto-summary').onchange=providerNotes;$('#stop').onclick=stopLive;
window.addEventListener('beforeunload',event=>{if(recording){event.preventDefault();event.returnValue='';}});
setInterval(async()=>{if(polling||recording)return;polling=true;try{if(current&&busyStatus(current)){const id=current.id;const updated=await api('/meetings/'+id);if(current?.id===id){current=updated;renderMeeting();}}else if(!current)await refreshList();}catch(e){console.warn('Refresh unavailable');}finally{polling=false;}},3000);
refreshList().catch(e=>toast(e.message));
