import { popup } from './popup.js';
import { sourceSelection } from './live-source-policy.js';
const uiText = (en, vi) => localStorage.getItem('voxmeet-ui-language') === 'en' ? en : vi;
import { renderSummary } from './summary-view.js';
let submittingQuestion = false;
const $=s=>document.querySelector(s);
let socket,context,worklet,streams=[],nodes=[],active=false,stopping=false,sent=0,ack=0,turns=new Map(),savedId=new URLSearchParams(location.search).get('meeting')||sessionStorage.getItem('voxmeet-live-id');
const fail=message=>{$('#error').hidden=false;$('#error').textContent=message;};
async function cleanup(){active=false;for(const s of streams)for(const t of s.getTracks())t.stop();streams=[];for(const n of nodes)n.disconnect();nodes=[];if(context){const old=context;context=null;old.close().catch(()=>{});}active=false;$('#stop').disabled=true;$('#live-provider').disabled=false;$('#live-language').disabled=false;$('#mic').disabled=false;$('#system').disabled=false;$('#source-mode').disabled=false;$('#start').disabled=socket?.readyState===0||socket?.readyState===1;$('#new-meeting').disabled=$('#start').disabled;$('#start').textContent=savedId?'Resume meeting':'Start meeting';}
let liveTranslations=new Map();
let configuringTranslation=false;
function bilingualMode(){return $('#live-translation-mode').value!=='off';}
function updateBilingualLayout(){
 const enabled=bilingualMode();$('#turns').classList.toggle('bilingual',enabled);$('#bilingual-head').hidden=!enabled;
 const direction=$('#live-translation-mode').value;
 $('#original-heading').textContent=direction==='en-vi'?'English':'Tiếng Việt';
 $('#translated-heading').textContent=direction==='en-vi'?'Tiếng Việt':'English';
 $('#live-translation-label').textContent=uiText('Parallel translation','Dịch song song');
 $('#live-translation-mode').options[0].textContent=uiText('Off','Tắt dịch');
 $('#live-translation-retry').textContent=uiText('Retry','Thử lại');
 for(const turn of turns.values())render({...turn,_force:true});
}
async function configureTranslation(){
 updateBilingualLayout();if(!savedId||configuringTranslation)return;
 configuringTranslation=true;$('#live-translation-mode').disabled=true;$('#live-translation-status').textContent=uiText('Connecting translator…','Đang kết nối bộ dịch…');
 try{
  const response=await fetch(`/api/recordings/${savedId}/live-translation`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({direction:$('#live-translation-mode').value})});
  const data=await response.json();if(!response.ok)throw Error(data.detail||'Translation unavailable');
  await refreshMeeting();
 }catch(e){$('#live-translation-status').textContent=e.message;$('#live-translation-retry').hidden=false;}
 finally{configuringTranslation=false;$('#live-translation-mode').disabled=false;}
}
$('#live-translation-mode').onchange=()=>{
 liveTranslations.clear();
 if(!active&&bilingualMode())$('#live-language').value=$('#live-translation-mode').value.split('-')[0];
 configureTranslation();
};
$('#live-translation-retry').onclick=configureTranslation;
let paintPending=false;
let gpuTiming=null;
function syncSources(){
  const selected=sourceSelection($('#source-mode').value);
  $('#mic').checked=selected[0];$('#system').checked=selected[1];
  $('#source-mode-label').textContent=uiText('Audio source','Nguồn thu');
  const options=$('#source-mode').options;
  options[0].textContent=uiText('Tab / system audio','Âm thanh tab / hệ thống');
  options[1].textContent=uiText('Room microphone','Mic trong phòng');
  options[2].textContent=uiText('Combine microphone + tab','Kết hợp mic + tab');
}
$('#source-mode').onchange=syncSources;syncSources();
function render(turn){
 const translated=liveTranslations.get(turn.id);
 turn={...turn,_translationText:bilingualMode()&&translated?.source_text===turn.text?translated.text:null};
 const old=turns.get(turn.id);if(!turn._force&&!turn.deleted&&old&&old._translationText===turn._translationText&&old.text===turn.text&&old.committed===turn.committed&&old.end===turn.end&&JSON.stringify(old.speaker_spans)===JSON.stringify(turn.speaker_spans)&&old.speaker===turn.speaker)return;
 if(turn.deleted)turns.delete(turn.id);else turns.set(turn.id,turn);if(paintPending)return;paintPending=true;
 requestAnimationFrame(()=>{paintPending=false;const container=$('#turns');const bottom=container.scrollHeight-container.scrollTop-container.clientHeight<40;
 container.replaceChildren(...[...turns.values()].sort((a,b)=>a.start-b.start).map(t=>{const el=document.createElement('div');el.className='live-turn'+(t.committed?'':' pending');const label=document.createElement('small');label.textContent=`${Math.floor(t.start/60)}:${String(Math.floor(t.start%60)).padStart(2,'0')} · ${t.source_channel===0?uiText('Room microphone','Mic trong phòng'):t.source_channel===1?uiText('Meeting audio','Âm thanh cuộc họp'):uiText('Transcript','Transcript')} · ${t.committed?uiText('Saved draft','Bản nháp đã lưu'):uiText('Draft · revising','Chữ tạm · đang cập nhật')}`;const p=document.createElement('p');p.textContent=t.text;const original=document.createElement('div');original.className='live-original';original.append(label,p);el.append(original);
 if(bilingualMode()){const translation=document.createElement('div');translation.className='live-translated';translation.lang=$('#live-translation-mode').value.split('-')[1];
 const row=liveTranslations.get(t.id);const valid=row?.source_text===t.text;
 const heading=document.createElement('small');heading.textContent=(translation.lang==='vi'?'Tiếng Việt':'English')+(row?.draft?uiText(' · Draft',' · Bản dịch nháp'):'');
 const text=document.createElement('p');text.textContent=valid?row.text:t.committed?uiText('Waiting for translation…','Đang chờ bản dịch…'):uiText('Translating draft…','Đang dịch lời nói…');
 translation.classList.toggle('translation-pending',!valid);translation.append(heading,text);el.append(translation);}return el;}));if(bottom)container.scrollTop=container.scrollHeight;
 });
}
async function stop(){if(!active||stopping)return;stopping=true;$('#stop').disabled=true;$('#state').textContent='Saving received audio and transcript…';if(worklet)worklet.port.postMessage('stop');else if(socket?.readyState===1)socket.send(JSON.stringify({type:'stop'}));}
$('#stop').onclick=stop;
$('#start').onclick=async()=>{
  $('#error').hidden=true;$('#saved').hidden=true;$('#start').disabled=true;sent=0;ack=0;stopping=false;worklet=null;gpuTiming=null;
  syncSources();
  const enabled=sourceSelection($('#source-mode').value);
  if(!enabled.some(Boolean)){fail('Choose at least one source.');$('#start').disabled=false;return;}
  $('#live-provider').disabled=true;$('#live-language').disabled=true;$('#mic').disabled=true;$('#system').disabled=true;$('#source-mode').disabled=true;$('#new-meeting').disabled=true;
  try{
    // Display capture requires the button's user gesture: request it first.
    let display=null,mic=null;
    if(enabled[1]){display=await navigator.mediaDevices.getDisplayMedia({video:true,audio:true,systemAudio:'include'});streams.push(display);if(!display.getAudioTracks().length)throw Error('No meeting audio was shared. Start again and enable Share audio, or select microphone only.');}
    if(enabled[0]){mic=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});streams.push(mic);}
    context=new AudioContext({sampleRate:16000});await context.resume();
    if(context.sampleRate!==16000)throw Error('This browser cannot capture at 16 kHz. Please use Chrome or Edge.');
    await context.audioWorklet.addModule('/live-worklet.js');
    socket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/api/live/stream`);
    active=true;
    socket.onopen=()=>socket.send(JSON.stringify({sources:enabled,resume_id:savedId,provider:$('#live-provider').value,language:$('#live-language').value}));
    socket.onmessage=async event=>{
      const message=JSON.parse(event.data);
      if(message.type==='loading')$('#state').textContent='Đang chuẩn bị nhận diện…';
      if(message.type==='notice')$('#state').textContent=message.message;
      if(message.type==='ready'){
        setTimeout(()=>{if(bilingualMode())configureTranslation();},0);
        if(message.caption_mode==='revisable')$('#timing').textContent=uiText('Live captions can revise; mic/tab are audio sources. Review speakers in Recordings.','Chữ tạm sẽ cập nhật; mic/tab là nguồn âm thanh. Phân nhãn người nói trong Bản ghi.');
        savedId=message.id;rememberMeeting();sessionStorage.setItem('voxmeet-live-id',savedId);sessionStorage.removeItem('voxmeet-live-new');sent=ack=message.frames||0;
        if(stopping){socket.send(JSON.stringify({type:'stop'}));return;}
        if(!streams.some(s=>s.getAudioTracks().some(t=>t.readyState==='live'))){stop();return;}
        worklet=new AudioWorkletNode(context,'live-capture',{numberOfInputs:2,numberOfOutputs:1,outputChannelCount:[1],channelCount:1,channelCountMode:'explicit'});
        worklet.port.onmessage=e=>{
          if(e.data.packet&&socket.readyState===1){const data=new Int16Array(e.data.packet);const levels=[0,0];for(let i=0;i<data.length;i++)levels[i%2]=Math.max(levels[i%2],Math.abs(data[i])/32768);$('#mic-level').value=levels[0];$('#system-level').value=levels[1];
            if(socket.bufferedAmount>512000||sent-ack>128000){fail('Processing cannot keep up. Capture stopped to avoid an unbounded delay. Received audio will be saved.');stop();return;}
            socket.send(e.data.packet);sent+=data.length/2;}
          if(e.data.stopped){if(socket.readyState===1)socket.send(JSON.stringify({type:'stop'}));cleanup();}
        };
        [mic,display].forEach((s,i)=>{if(s){const input=context.createMediaStreamSource(new MediaStream(s.getAudioTracks()));input.connect(worklet,0,i);nodes.push(input);}});
        const mute=context.createGain();mute.gain.value=0;worklet.connect(mute);mute.connect(context.destination);nodes.push(worklet,mute);
        $('#state').textContent='Đang ghi âm · transcript là bản nháp';$('#stop').disabled=false;
      }
      if(message.type==='update'){
        ack=message.frames;if(message.speakers){if(!meeting)meeting={};meeting.speakers={...(meeting.speakers||{}),...message.speakers};}(message.remove_turns||[]).forEach(id=>render({id,deleted:true}));message.turns.forEach(render);
        if(message.timing)gpuTiming=message.timing;
        const parts=[uiText('Received ','Đã nhận ')+(ack/16000).toFixed(1)+'s'];
        if(gpuTiming){
          if(gpuTiming.asr_model)parts.push('Whisper '+gpuTiming.asr_model);
          parts.push(uiText('GPU round trip ','Chờ GPU và nhận kết quả ')+gpuTiming.remote_seconds.toFixed(1)+'s');
          parts.push(uiText('Queued jobs ','Đoạn đang chờ ')+gpuTiming.queue_depth);
        }
        const timingBase=parts.join(' · ');
        const lagLabel=uiText('Last text update behind captured audio ≈ ','Lần hiện chữ vừa rồi chậm hơn audio ≈ ');
        $('#timing').textContent=timingBase+(gpuTiming?.display_lag!=null?' · '+lagLabel+gpuTiming.display_lag.toFixed(1)+'s':'');
        if(message.turns.length){
          const end=Math.max(...message.turns.map(t=>t.end));
          requestAnimationFrame(()=>{
            const lag=Math.max(0,sent/16000-end);
            if(gpuTiming)gpuTiming.display_lag=lag;
            $('#timing').textContent=timingBase+' · '+lagLabel+lag.toFixed(1)+'s';
          });
        }
      }
      if(message.type==='error'){fail(message.message);stop();}
      if(message.type==='saved'){savedId=message.id;$('#state').textContent=uiText('Paused · saved ', 'Đã tạm dừng · đã lưu ')+(message.duration/60).toFixed(1)+uiText(' minutes',' phút');$('#saved').href=`/#recording=${message.id}`;$('#saved').hidden=false;sessionStorage.setItem('voxmeet-live-id',savedId);sessionStorage.removeItem('voxmeet-live-new');await cleanup();await refreshMeeting();}
    };
    socket.onerror=()=>fail('Live connection failed. Check that the demo is running.');
    socket.onclose=async()=>{if(!stopping&&active)fail('Connection ended. Check Recordings for the received audio.');await cleanup();};
    for(const s of streams)for(const track of s.getTracks())track.onended=()=>{if(active&&!stopping){fail('An audio share or microphone ended. Saving this meeting.');stop();}};
  }catch(e){fail(e.message);await cleanup();}
};
addEventListener('beforeunload',e=>{if(active){e.preventDefault();e.returnValue='';}});
async function refreshLiveReady(){
 if(active)return;
 const s=await fetch('/api/live/status').then(r=>r.json());
 const gpu=$('#live-provider').value==='kaggle';
 const ready=gpu?s.gpu_ready:s.ready&&$('#live-language').value==='en';
 $('#start').disabled=!ready;
 $('#state').textContent=ready?uiText('Ready','Sẵn sàng'):gpu&&s.gpu_state==='warming'?uiText('Preparing speech and chat models on Kaggle…','Đang chuẩn bị model nhận dạng và chat trên Kaggle…'):gpu&&s.gpu_state==='error'?uiText('Model preparation failed. Check Kaggle output.','Chuẩn bị model thất bại. Kiểm tra output Kaggle.'):gpu?uiText('Worker is not responding. Check the existing Kaggle session.','Worker mất phản hồi. Kiểm tra phiên Kaggle đang dùng.'):'CPU cũ chỉ hỗ trợ English; tiếng Việt cần Kaggle.';
}
$('#live-provider').onchange=()=>refreshLiveReady().catch(e=>fail(e.message));
$('#live-language').onchange=()=>refreshLiveReady().catch(e=>fail(e.message));
addEventListener('voxmeet-worker-status',()=>refreshLiveReady().catch(()=>{}));
refreshLiveReady().catch(e=>fail(e.message));

let meeting=null,assistantKey='',polling=false;
const clock=t=>`${Math.floor((t||0)/60)}:${String(Math.floor((t||0)%60)).padStart(2,'0')}`;
function navigation(library){
 document.body.classList.toggle('library-view',library);
 $('#show-recordings').classList.toggle('active',library);$('#show-live').classList.toggle('active',!library);
 $('#workspace-title').textContent=library?'Workspace / Recordings':'Workspace / Live meeting';
 window.scrollTo(0,0);
}
function showLive(){$('#library-panel').hidden=true;$('#live-panel').hidden=false;navigation(false);if(savedId)refreshMeeting();}
async function refreshSetup(){
 const response=await fetch('/api/health');if(!response.ok)throw Error('Could not load setup status.');const caps=await response.json();
 $('#connections').replaceChildren(...[
 ['Audio decoder (FFmpeg)',caps.ffmpeg_available],['Local speech libraries',caps.local_installed],
 ['English transcription model',caps.local_models?.asr],['Speaker recognition models',caps.local_models?.segmentation&&caps.local_models?.embedding],
 ['Deepgram API key (optional)',caps.deepgram_configured]
 ].map(([label,ok])=>{const row=document.createElement('div');row.className='connection';const name=document.createElement('strong');name.textContent=label;const status=document.createElement('span');status.textContent=ok?'Available':'Not configured';status.className=ok?'ok':'';row.append(name,status);return row;}));
}
async function openSetup(){const dialog=$('#setup-dialog');if(!dialog.open)dialog.showModal();try{await refreshSetup();window.dispatchEvent(new Event("voxmeet-refresh-worker"));}catch(e){$('#connections').textContent=e.message;}}
$('#setup').onclick=openSetup;
$('#close-setup').onclick=()=>$('#setup-dialog').close();
$('#refresh-setup').onclick=()=>refreshSetup().catch(e=>{$('#connections').textContent=e.message;});
function showRecordings(path='/recordings'){
  $('#live-panel').hidden=true;$('#library-panel').hidden=false;navigation(true);
  loadSavedMeetings();
  const frame=$('#recordings-frame');if(!frame.getAttribute('src')||path!=='/recordings')frame.src=path;else frame.contentWindow.postMessage({type:'voxmeet-refresh-recordings'},location.origin);
}
$('#show-recordings').onclick=e=>{e.preventDefault();showRecordings();};
$('.brand').onclick=()=>{};
$('#show-live').onclick=showLive;
$('#saved').onclick=e=>{e.preventDefault();if(savedId)showRecordings('/#recording='+savedId);};
addEventListener('message',e=>{if(e.origin===location.origin&&e.source===$('#recordings-frame').contentWindow&&e.data?.type==='voxmeet-show-live')showLive();});
$('#new-meeting').onclick=()=>{
  if(active||socket?.readyState===1)return;
  savedId=null;meeting=null;gpuTiming=null;liveTranslations.clear();$('#live-translation-status').textContent='';turns.clear();assistantKey='';sessionStorage.removeItem('voxmeet-live-id');sessionStorage.setItem('voxmeet-live-new','1');history.replaceState(null,'','/live');
  for(const id of ['turns','live-chat','live-summary','summary-history'])$('#'+id).replaceChildren();
  $('#delete-live').hidden=true;$('#saved').hidden=true;$('#session-name').textContent='New meeting — ready to start';$('#start').textContent='Start meeting';
  $('#live-ask').disabled=$('#live-summarize').disabled=true;$('#state').textContent='Previous meetings remain in Recordings.';
  $('#question-state').textContent=$('#summary-state').textContent='';
  $('#assistant-coverage').textContent='';$('#timing').textContent='';$('#error').hidden=true;
  refreshLiveReady().catch(error=>fail(error.message));
};
function rememberMeeting(){
 if(!savedId)return;
 sessionStorage.setItem('voxmeet-live-id',savedId);sessionStorage.removeItem('voxmeet-live-new');
 history.replaceState(null,'','/live?meeting='+encodeURIComponent(savedId));
}
async function loadSavedMeetings(){
 try {
  const response = await fetch('/api/recordings');
  if (!response.ok) throw Error('Could not load recordings.');
  const rows = await response.json();
  $('#count').textContent = rows.length;
 } catch (error) { fail(error.message); }
}
function paragraph(text){const p=document.createElement('p');p.textContent=text;return p;}
function sources(ids){const details=document.createElement('details');const title=document.createElement('summary');title.textContent=uiText('Supporting transcript','Dẫn chứng');details.append(title);
  for(const id of ids){const s=meeting.segments.find(s=>s.id===id);if(s)details.append(paragraph(`[${clock(s.start)}] ${s.text}`));else details.append(paragraph(uiText('Source unavailable in the current transcript. Generate a new answer.','Không tìm thấy dẫn chứng trong transcript hiện tại. Hãy hỏi lại.')));}return details;}
function summaryView(summary){
  const box = renderSummary(summary, sources);
  box.prepend(paragraph(uiText('Based on transcript through ','Dựa trên transcript đến ')+clock(summary.through_seconds)));
  return box;
}
let meetingVersion=null;
function renderAssistantTimers(data){
  $('#summary-state').textContent=data.summary_status==='processing'?uiText('Summarizing through ','Đang tóm tắt đến ')+clock(data.summary_pending_through)+' · '+Math.max(0,Math.floor(Date.now()/1000-(data.summary_started_at||Date.now()/1000)))+'s':data.summary_error||'';
  $('#question-state').textContent=data.qa_status==='processing'?uiText('Answering through ','Đang trả lời đến ')+clock(data.qa_pending_through)+' · '+Math.max(0,Math.floor(Date.now()/1000-(data.qa_started_at||Date.now()/1000)))+'s: '+(data.qa_pending||''):data.qa_error||'';
}
async function refreshMeeting(){
  if(!savedId||polling)return;polling=true;const requested=savedId;
  try{
    // Conditional request: unchanged meeting -> 304, no download, no re-render.
    const known=meetingVersion?.id===requested&&meeting?.id===requested?meetingVersion.etag:null;
    const r=await fetch('/api/recordings/'+requested,{cache:'no-store',headers:known?{'If-None-Match':known}:{}});
    if(r.status===304&&meeting?.id===requested){renderAssistantTimers(meeting);return;}
    if(!r.ok)throw Error('Could not restore this meeting. Saved recordings were not deleted.');
    const data=await r.json();if(savedId!==requested)return;meeting=data;
    const etag=r.headers.get('ETag');meetingVersion=etag?{id:requested,etag}:null;$('#delete-live').hidden=false;$('#delete-live').disabled=active||data.qa_status==='processing'||data.summary_status==='processing';$('#session-name').textContent=data.title;
    const translationState=data.live_translation;
    liveTranslations=new Map((translationState&&translationState.source_language+'-'+translationState.target_language===$('#live-translation-mode').value?translationState.segments||[]:[]).map(row=>[row.id,row]));
    if(translationState?.enabled&&translationState.source_language+'-'+translationState.target_language===$('#live-translation-mode').value){
      for(const row of data.live_translation_previews||[])liveTranslations.set(row.id,row);
    }
    for(const turn of turns.values())if(!turn.committed)render({...turn});
    if(translationState&&!configuringTranslation){
      const direction=translationState.enabled?translationState.source_language+'-'+translationState.target_language:'off';
      if($('#live-translation-mode').value!==direction){$('#live-translation-mode').value=direction;updateBilingualLayout();}
    }
    const statuses={waiting:uiText('Waiting for text','Chờ lời nói'),listening:uiText('Listening · translation up to date','Đang nghe · đã dịch các câu hiện có'),translating:uiText('Translating on Kaggle…','Đang dịch trên Kaggle…'),ready:uiText('Bilingual transcript saved','Đã lưu transcript song ngữ'),off:''};
    $('#live-translation-status').textContent=translationState?.error||statuses[translationState?.status]||'';
    $('#live-translation-retry').hidden=translationState?.status!=='error';
    if(!active)turns.clear();
    for(const turn of data.segments||[])render({...turn,committed:true});
    const end=Math.max(0,...(data.segments||[]).map(s=>s.end));
    $('#assistant-coverage').textContent=uiText('Settled transcript through','Transcript đã chốt đến')+' '+clock(end);
    const busy=submittingQuestion||data.qa_status==='processing'||data.summary_status==='processing';
    $('#live-ask').disabled=$('#live-summarize').disabled=!data.segments?.length||busy;
    renderAssistantTimers(data);
    const key=JSON.stringify([data.qa,data.summary,data.summary_history,data.qa_pending,data.qa_status,data.qa_error]);
    if(key!==assistantKey && !submittingQuestion){assistantKey=key;$('#live-chat').replaceChildren();
      for(const q of data.qa||[]){const block=document.createElement('article');block.append(paragraph(uiText('You: ','Bạn: ')+q.question));
        if(q.answerable)for(const point of q.points)block.append(paragraph(point.text),sources(point.evidence.map(e=>e.id)));
        else block.append(paragraph(uiText('Not enough transcript evidence to answer confidently.','Transcript hiện tại chưa đủ thông tin để trả lời chắc chắn.')));
        block.append(paragraph(uiText('Transcript through ','Transcript đến ')+clock(q.through_seconds)));$('#live-chat').append(block);}
      if(data.qa_pending && ['processing','error'].includes(data.qa_status)) showPendingQuestion(data.qa_pending, data.qa_error || uiText('Waiting for GPU response…','Đang chờ GPU trả lời…'));
      $('#live-summary').replaceChildren(...(data.summary?[summaryView(data.summary)]:[]));
      $('#summary-history').replaceChildren(...(data.summary_history||[]).filter(s=>s.revision===data.revision).slice(0,-1).map(summaryView));
    }
  }catch(e){fail(e.message);}finally{polling=false;}
}
async function requestAssistant(route,body){
  if(!savedId)return;$('#live-ask').disabled=$('#live-summarize').disabled=true;
  const r=await fetch(`/api/recordings/${savedId}/${route}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!r.ok){const error=await r.json();throw Error(error.detail||'Request failed.');}await refreshMeeting();
}
$('#live-summarize').onclick=async()=>{try{await requestAssistant('summary',{provider:$('#live-assistant-provider').value,language:$('#live-summary-language').value});}catch(e){fail(e.message);await refreshMeeting();}};
function showPendingQuestion(question, status){
 const chat=$('#live-chat');chat.querySelector('[data-pending-question]')?.remove();
 const block=document.createElement('article');block.dataset.pendingQuestion='true';
 const label=document.createElement('strong');label.textContent=window.voxmeetT?.('Your question')||'Your question';
 const state=paragraph(status);state.setAttribute('role','status');
 block.append(label,paragraph(question),state);chat.append(block);chat.scrollTop=chat.scrollHeight;
}
$('#live-question-form').onsubmit=async e=>{
 e.preventDefault();const input=$('#live-question'),question=input.value.trim();
 if(!question||submittingQuestion)return;
 if(!savedId){$('#question-state').textContent='Start or open a meeting first.';return;}
 submittingQuestion=true;showPendingQuestion(question,'Sending question…');
 $('#question-state').textContent='Sending question…';
 try{
  await requestAssistant('questions',{provider:$('#live-assistant-provider').value,question});
  if(input.value.trim()===question)input.value='';
 }catch(e){showPendingQuestion(question,e.message);$('#question-state').textContent=e.message;}
 finally{submittingQuestion=false;await refreshMeeting();}
};
async function restoreSession(){
  const health = await fetch('/api/health').then(response => response.json());
  const oldServer = sessionStorage.getItem('voxmeet-server-session');
  if (oldServer !== health.server_session) {
    sessionStorage.removeItem('voxmeet-live-id');
    savedId = null;
    history.replaceState(null, '', '/live');
  }
  sessionStorage.setItem('voxmeet-server-session', health.server_session);
  if(savedId){rememberMeeting();await refreshMeeting();if(meeting){$('#live-provider').value=meeting.provider==='live-kaggle'?'kaggle':'local';$('#live-language').value=meeting.language||'en';await refreshLiveReady();}$('#start').textContent='Resume meeting';$('#saved').hidden=false;$('#saved').href='/#recording='+savedId;}
}
restoreSession().then(loadSavedMeetings).catch(error=>fail(error.message));
// Faster refresh while translations drain, including after recording stops.
let lastMeetingPoll=0;
setInterval(()=>{
  const state=meeting?.live_translation;
  const translating=state?.enabled&&!['ready','error','off'].includes(state.status);
  const now=Date.now();
  // Fast while something is changing; slow when the meeting is idle.
  const busy=active||submittingQuestion||meeting?.qa_status==='processing'||meeting?.summary_status==='processing'||meeting?.translation_status==='processing'||['recording','queued','decoding','transcribing','cancelling'].includes(meeting?.status);
  const interval=translating?250:busy?1000:5000;
  if(document.hidden&&!active)return;
  if(savedId&&now-lastMeetingPoll>=interval){
    lastMeetingPoll=now;refreshMeeting();
  }
},250);

async function applyRecordingChange(change){
 if(change.id===savedId){
  if(change.deleted){if(!active)$('#new-meeting').click();}
  else await refreshMeeting();
 }
 await loadSavedMeetings();
}
addEventListener('message',e=>{if(e.origin===location.origin&&e.source===$('#recordings-frame').contentWindow&&e.data?.type==='voxmeet-recording-changed')applyRecordingChange(e.data);});
addEventListener('storage',e=>{if(e.key==='voxmeet-recording-change'&&e.newValue){try{applyRecordingChange(JSON.parse(e.newValue));}catch(e){}}});

$('#delete-live').onclick = async () => {
 if (!savedId || active) return;
 if (!(await popup({title:uiText('Delete meeting?','Xóa cuộc họp?'),message:uiText('Delete audio, transcript, summaries and chat?','Xóa audio, transcript, tóm tắt và chat?'),destructive:true}))) return;
 try {
  const response = await fetch('/api/recordings/' + savedId, {method:'DELETE'});
  if (!response.ok) throw Error((await response.json()).detail || 'Request failed.');
  localStorage.setItem('voxmeet-recording-change', JSON.stringify({id:savedId,deleted:true,at:Date.now()}));
  $('#new-meeting').click();
  await loadSavedMeetings();
 } catch (error) { fail(error.message); }
};
