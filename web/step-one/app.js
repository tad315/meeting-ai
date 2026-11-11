import { popup } from './popup.js';
const uiText = (en, vi) => localStorage.getItem('voxmeet-ui-language') === 'en' ? en : vi;
import { renderSummary as renderSummaryContent } from './summary-view.js';
const $ = selector => document.querySelector(selector);
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const clock = value => {const n=Math.floor(value||0);return `${String(Math.floor(n/60)).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`;};
const active = status => ['recording','queued','decoding','transcribing','cancelling'].includes(status);
const labels = {recording:'Recording live',queued:'Waiting',decoding:'Preparing audio',uploaded:'Ready to transcribe',transcribing:'Transcribing',cancelling:'Cancelling',cancelled:'Cancelled',ready:'Transcript ready',error:'Needs attention',interrupted:'Interrupted'};
const stages={waiting_gpu_connection:uiText('Waiting for the GPU connection; the job is preserved…','Đang chờ GPU kết nối lại; tác vụ vẫn được giữ…'),verifying_speakers:uiText('Reviewing speaker labels…','Đang kiểm tra nhãn người nói…'),returning_result:uiText('Returning transcript…','Đang nhận transcript…'),community_worker:'Community-1 worker is processing your recording…',loading_models:'Loading local speech models…',recognizing_speech:'Recognizing English speech',identifying_speakers:'Identifying speakers',aligning_words:'Matching words to speaker turns…',saving:'Saving your transcript…',requesting_api:'Waiting for the speech API…'};
const colours=['#e8eed9','#dfecef','#efe5da','#eae2ef','#f1edcf'];
let recordings=[], current=null, caps=null, uploading=false, polling=false;
async function api(path, options={}){
  const response=await fetch('/api'+path,options);const data=await response.json();
  if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'The request could not be completed.');
  return data;
}
function toast(message){$('#toast').textContent=message;$('#toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('#toast').hidden=true,8000);}
function renderList(){
  $('#count').textContent=recordings.length;$('#total').textContent=recordings.length;
  const term=$('#search').value.toLowerCase(), rows=recordings.filter(r=>r.title.toLowerCase().includes(term));
  $('#recordings').innerHTML=rows.map(r=>`<div class="recording-row"><button class="recording" data-id="${escape(r.id)}"><span class="recording-icon" aria-hidden="true">≋</span><div><strong>${escape(r.title)}</strong><small>${escape(new Date(r.created_at).toLocaleDateString(localStorage.getItem('voxmeet-ui-language')==='en'?'en':'vi',{month:'short',day:'numeric'}))} · ${clock(r.duration)} · ${escape(r.language||'en')}</small></div><span class="tag ${escape(r.status)}">${escape(window.voxmeetT?.(labels[r.status]||r.status)||labels[r.status]||r.status)}</span><span aria-hidden="true">↗</span></button><button type="button" class="icon-action danger" data-delete="${escape(r.id)}" title="${uiText('Delete','Xóa')}" aria-label="${uiText('Delete recording','Xóa bản ghi')}"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/></svg></button></div>`).join('')||`<div class="empty"><span aria-hidden="true">≋</span><h3>${term?'No matching recordings.':'Your first conversation starts here.'}</h3><p>${term?'Try a different title.':'Upload an English audio file to get started.'}</p></div>`;
}
async function refreshList(){recordings=await api('/recordings');renderList();}
function refreshMode(){
  const provider=$('#provider').value;
  const ready=caps&&(provider==='community'?caps.community_worker?.available_for_jobs:provider==='local'?caps.local_ready:caps.deepgram_configured);
  $('#record-turn-policy').hidden=provider!=='community';document.querySelector('label[for="record-turn-policy"]').hidden=provider!=='community';
  $('#provider-note').textContent=provider==='community'?'Whisper đa ngôn ngữ + Community-1 (VBx) on your selected GPU worker. Audio is transferred through Cloudflare. Review speaker labels after processing.':provider==='local'?'Legacy CPU mode may split one person into several speakers. Use GPU transcript below for Community-1.':'Audio will be sent to Deepgram to transcribe and separate speakers. Provider charges may apply.';
  $('#transcribe').disabled=!current?.has_audio||active(current?.status)||!ready;
  $('#provider').disabled=active(current?.status);$('#audio-language').disabled=active(current?.status);
  $('#speaker-count').disabled=active(current?.status)||provider==='deepgram';
  if(provider==='community'&&$('#record-turn-policy').value==='shared-sentence-v1')$('#provider-note').textContent=uiText('Live sentence splitting with full-recording Community-1 / VBx speaker labels.','Chia câu như Live, giữ nhãn phân cụm Community-1 / VBx trên toàn bản ghi.');
  $('#transcribe').textContent=current?.segments?.length?'Reprocess audio (replace transcript) →':'Create transcript →';
  if(current&&!active(current.status))$('#status').textContent=!ready?'Set up this processing mode before creating a transcript.':current.status==='ready'?'Transcript ready. Compare the words with the original audio.':'Audio prepared. Choose a mode and create your transcript.';
}
function renderDetail(){
  if(!current)return;
  const segments=current.segments||[], speakers=current.speakers||{};
  $('#breadcrumb').textContent='Recording';$('#recording-title').textContent=current.title;
  $('#recording-meta').textContent=`${current.filename||'Audio recording'} · ${clock(current.duration)} · ${current.provenance?.language||current.language||'en'}`;
  $('#player').hidden=!current.has_audio;
  const audioPath=`/api/recordings/${current.id}/audio`;
  if(current.has_audio&&$('#audio').getAttribute('src')!==audioPath)$('#audio').src=audioPath;
  const stageLabel=stages[current.stage]||'Processing your recording…';
  $('#status').textContent={queued:'Waiting for the current processing task…',decoding:'Preparing audio for playback and speech recognition…',transcribing:stageLabel+(current.progress!=null?` · ${current.progress}%`:''),cancelling:'Cancelling after the current model operation finishes…'}[current.status]||'';
  $('#progress').hidden=current.status!=='transcribing'||current.progress==null;
  $('#progress').value=current.progress||0;
  $('#cancel').hidden=!['queued','transcribing','cancelling'].includes(current.status)||current.stage==null;
  $('#cancel').disabled=current.status==='cancelling';
  $('#error').hidden=!current.error;
  $('#error').innerHTML=escape(current.error||'')+(!current.has_audio&&current.error?'<button id="retry-prepare" class="text-button">Retry preparation</button>':'');
  $('#warning').hidden=!current.warning;$('#warning').textContent=current.warning||'';
  $('#export').hidden=current.status!=='ready';$('#export').href=`/api/recordings/${current.id}/export`;
  $('#stats').textContent=`${Object.keys(speakers).length} ${uiText('speakers','người nói')} · ${segments.length} ${uiText('turns','lượt nói')}`;
  $('#speaker-legend').innerHTML=Object.entries(speakers).map(([id,name])=>`<span class="speaker-chip" style="background:${colours[Number(id)%colours.length]}">● ${escape(name)}</span>`).join('');
  $('#transcript').innerHTML=segments.map(s=>`<article class="turn" data-start="${Number(s.start)}" data-end="${Number(s.end)}"><button class="time" data-time="${Number(s.start)}" aria-label="Play from ${clock(s.start)}">${clock(s.start)}</button><div><span class="speaker" style="background:${colours[Number(s.speaker||0)%colours.length]}">${escape(speakers[s.speaker]||'Unassigned')}</span><p>${escape(s.text)}</p></div></article>`).join('')||`<div class="empty"><span aria-hidden="true">≋</span><h3>${active(current.status)?'Your recording is being processed.':current.status==='ready'?'No speech detected.':'A place for every voice.'}</h3><p>${active(current.status)?'The transcript will appear here when processing finishes.':current.status==='ready'?'Try a recording with clearer speech.':'Create a transcript to see speaker-labelled turns and timestamps.'}</p></div>`;
  refreshMode();
}
async function openRecording(id){
  const item=await api('/recordings/'+id);current=item;
  history.replaceState(null,'',`#recording=${encodeURIComponent(id)}`);
  $('#library').hidden=true;$('#detail').hidden=false;$('#provider').value=item.provider==='community'||item.provider==='deepgram'?item.provider:'community';$('#speaker-count').value=item.num_speakers||'';$('#audio-language').value=item.language||'auto';$('#record-turn-policy').value=item.turn_policy||'shared-sentence-v1';renderDetail();
}
async function refreshCaps(){
  caps=await api('/health');
  const hasProvider=caps.deepgram_configured||caps.local_ready||caps.community_worker?.available_for_jobs;
  $('#community-status').textContent=caps.community_worker?.startup_state==='warming'?uiText('Preparing speech and chat models on Kaggle…','Kaggle đang tải/nạp model nhận dạng và chat…'):caps.community_worker?.available_for_jobs?uiText('Kaggle is online.','Kaggle đang online.'):uiText('Worker is not ready — check the Kaggle cell.','Worker chưa sẵn sàng — kiểm tra cell Kaggle.');
  $('#community-notebook').hidden=!caps.community_worker?.configured;
  $('#readiness').hidden=hasProvider&&caps.ffmpeg_available;
  $('#readiness-text').textContent=!caps.ffmpeg_available?'The audio decoder needs setup before uploading.':'You can upload and play audio now. Configure local models or an API key to create speaker-labelled transcripts.';
  $('#choose-file').disabled=uploading||!caps.ffmpeg_available;
  $('#connections').innerHTML=[['Audio decoder (FFmpeg)',caps.ffmpeg_available,'Available','Not found'],['Local speech libraries',caps.local_installed,'Installed','Not installed'],['English transcription model',caps.local_models?.asr,'Downloaded','Not downloaded'],['Speaker recognition models',caps.local_models?.segmentation&&caps.local_models?.embedding,'Downloaded','Not downloaded'],['Deepgram API key (optional)',caps.deepgram_configured,'Configured','Not configured']].map(([label,ok,yes,no])=>`<div class="connection"><strong>${label}</strong><span class="${ok?'ok':''}">${ok?yes:no}</span></div>`).join('');
  if(current)refreshMode();
}
async function upload(file){
  if(!file||uploading)return;
  if(file.size>100*1024*1024){toast('Choose an audio file smaller than 100 MB.');return;}
  if(!/\.(wav|mp3|m4a|mp4|flac|ogg|webm)$/i.test(file.name)){toast('Choose a supported audio or video file.');return;}
  uploading=true;$('#choose-file').disabled=true;$('#upload-status').hidden=false;$('#upload-status').textContent=uiText('Uploading ','Đang tải lên ')+file.name+'…';
  try{const form=new FormData();form.append('file',file);const item=await api('/upload',{method:'POST',body:form});await refreshList();await openRecording(item.id);$('#upload-status').hidden=true;}
  catch(error){$('#upload-status').textContent=error.message;}
  finally{uploading=false;$('#choose-file').disabled=!caps?.ffmpeg_available;$('#file').value='';}
}
async function showSetup(){try{await refreshCaps();$('#setup-dialog').showModal();}catch(error){toast(error.message);}}
$('#choose-file').onclick=()=>$('#file').click();$('#file').onchange=()=>upload($('#file').files[0]);
$('#drop-zone').ondragover=event=>{event.preventDefault();$('#drop-zone').classList.add('dragover');};
$('#drop-zone').ondragleave=()=>$('#drop-zone').classList.remove('dragover');
$('#drop-zone').ondrop=event=>{event.preventDefault();$('#drop-zone').classList.remove('dragover');if(event.dataTransfer.files.length>1){toast('Please upload one recording at a time.');return;}upload(event.dataTransfer.files[0]);};
$('#search').oninput=renderList;$('#provider').onchange=refreshMode;
for(const id of ['setup','setup-inline','setup-detail'])$('#'+id).onclick=showSetup;
$('#close-setup').onclick=()=>$('#setup-dialog').close();$('#refresh-setup').onclick=()=>refreshCaps().catch(e=>toast(e.message));
function home(){current=null;history.replaceState(null,'',location.pathname);$('#audio').pause();$('#library').hidden=false;$('#detail').hidden=true;$('#breadcrumb').textContent='Recordings';refreshList().catch(e=>toast(e.message));}
$('#home').onclick=home;$('#back').onclick=home;
$('#transcribe').onclick=async()=>{
  if(!current)return;const id=current.id;$('#transcribe').disabled=true;
  try{const updated=await api(`/recordings/${id}/transcribe`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({provider:$('#provider').value,language:$('#audio-language').value,turn_policy:$('#provider').value==='community'?$('#record-turn-policy').value:'legacy',num_speakers:$('#provider').value!=='deepgram'&&$('#speaker-count').value?Number($('#speaker-count').value):null})});if(current?.id===id){current=updated;renderDetail();}}
  catch(error){toast(error.message);refreshMode();}
};
$('#cancel').onclick=async()=>{if(!current)return;const id=current.id;$('#cancel').disabled=true;try{const updated=await api(`/recordings/${id}/cancel`,{method:'POST'});if(current?.id===id){current=updated;renderDetail();}}catch(error){toast(error.message);$('#cancel').disabled=false;}};
document.addEventListener('click',async event=>{
  const button=event.target.closest('button');if(!button)return;
  try{
    if(button.dataset.id)await openRecording(button.dataset.id);
    if(button.dataset.time!==undefined){$('#audio').currentTime=Number(button.dataset.time);await $('#audio').play();}
    if(button.id==='retry-prepare'&&current){const id=current.id;const updated=await api(`/recordings/${id}/prepare`,{method:'POST'});if(current?.id===id){current=updated;renderDetail();}}
  }catch(error){toast(error.message);}
});
$('#audio').ontimeupdate=()=>{const time=$('#audio').currentTime;document.querySelectorAll('.turn').forEach(el=>el.classList.toggle('playing',time>=Number(el.dataset.start)&&time<Number(el.dataset.end)));};
setInterval(async()=>{
  if(polling||uploading)return;polling=true;
  try{
    if(current&&active(current.status)){const id=current.id,updated=await api('/recordings/'+id);if(current?.id===id){current=updated;renderDetail();}}
    else if(!current)await refreshList();
  }catch(error){toast('The server is unavailable. Start run.ps1 to reconnect.');}finally{polling=false;}
},2500);
Promise.all([refreshCaps(),refreshList()]).then(()=>{const id=new URLSearchParams(location.hash.slice(1)).get('recording');if(id)return openRecording(id);}).catch(error=>toast(error.message));

// Demo integration: original transcript remains the source of truth.
let editing=null, gpuConnecting=false;
function renderSummary(){
  if(!current)return;
  const busy=current.summary_status==='processing';
  $('#summarize').disabled=busy||active(current.status)||!current.segments?.length;
  $('#summary-provider').disabled=busy;
  $('#summary-note').textContent=$('#summary-provider').value==='kaggle' ? 'Tóm tắt bằng GPU đang chọn. Transcript được gửi tới worker; không dùng CPU máy này.' : $('#summary-provider').value==='api'
    ?'Transcript text will be sent to your configured API provider. Charges may apply.'
    :`Uses Ollama (${caps?.ollama_model||'configured model'}) on this computer. Start Ollama first; CPU processing may take several minutes.`;
  $('#summary-status').textContent=busy?'Creating a draft from the current transcript…':current.summary_error||'';
  const summary=current.summary?.revision===current.revision?current.summary:null;
  $('#summary-export').hidden=!summary;
  $('#summary-export').href=`/api/recordings/${current.id}/summary/export`;
  const container = $('#summary-result');
  if (!summary) {
    container.textContent = 'No draft yet. Review or edit the transcript, then generate a summary.';
  } else {
    container.replaceChildren(renderSummaryContent(summary, ids => {
      const details = document.createElement('details');
      const heading = document.createElement('summary');
      heading.textContent = 'Dẫn chứng';
      details.append(heading);
      for (const id of ids) {
        const segment = current.segments.find(row => row.id === id);
        if (!segment) continue;
        const quote = document.createElement('blockquote');
        if (current.has_audio) {
          const listen = document.createElement('button');
          listen.type = 'button';
          listen.className = 'text-button';
          listen.textContent = '▶ ' + clock(segment.start);
          listen.onclick = () => {
            const audio = $('#audio');
            audio.currentTime = segment.start;
            audio.play().catch(() => {});
          };
          quote.append(listen);
        }
        quote.append(document.createTextNode(` [${id}] ${clock(segment.start)} · ${segment.text}`));
        details.append(quote);
      }
      return details;
    }));
  }
}
const originalRenderDetail=renderDetail;
renderDetail=function(){
  originalRenderDetail();
  if(!current)return;
  document.querySelectorAll('#transcript .turn').forEach((article,index)=>{
    const s=current.segments[index];
    const button=document.createElement('button');
    button.className='text-button edit-turn';button.textContent=s.edited?'Edit · corrected':'Edit';
    button.disabled=active(current.status);
    button.onclick=()=>{
      editing={mid:current.id,sid:s.id,revision:current.revision};
      $('#edit-speaker').innerHTML='<option value="">Unassigned</option>'+Object.entries(current.speakers).map(([id,name])=>`<option value="${escape(id)}">${escape(name)}</option>`).join('');
      $('#edit-speaker').value=s.speaker??'';$('#edit-text').value=s.text;$('#edit-dialog').showModal();
    };
    article.querySelector('div').append(button);
    const splitButton=document.createElement('button');splitButton.className='text-button';splitButton.textContent='Split turn';
    splitButton.disabled=active(current.status)||s.text.trim().split(/\s+/).length<2;
    splitButton.onclick=()=>openSplit(s).catch(e=>toast(e.message));
    article.querySelector('div').append(splitButton);
    if(!current.has_audio)article.querySelector('.time').disabled=true;
  });
  renderSummary();
  renderQA();
  $('#gpu-provenance').textContent=current.provenance?`Current transcript: ${current.provenance.asr_model} + ${current.provenance.diarization_model}`:'Community-1 has not been applied to this recording.';
};
$('#summary-provider').onchange=renderSummary;
$('#summarize').onclick=async()=>{
  if(!current)return;
  const id=current.id;$('#summarize').disabled=true;
  try{const item=await api(`/recordings/${id}/summary`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({provider:$('#summary-provider').value,language:$('#summary-language').value})});if(current?.id===id){current=item;renderSummary();}}
  catch(e){toast(e.message);renderSummary();}
};
$('#import-colab').onclick=()=>$('#colab-file').click();
$('#colab-file').onchange=async()=>{
  const file=$('#colab-file').files[0];if(!file)return;
  if(file.size>5*1024*1024){toast('Maximum transcript JSON size is 5 MB.');return;}
  $('#import-colab').disabled=true;
  try{const form=new FormData();form.append('file',file);const item=await api('/import-transcript',{method:'POST',body:form});await refreshList();await openRecording(item.id);}
  catch(e){toast(e.message);}finally{$('#import-colab').disabled=false;$('#colab-file').value='';}
};
$('#cancel-edit').onclick=()=>$('#edit-dialog').close();
$('#edit-form').onsubmit=async event=>{
  event.preventDefault();if(!editing)return;
  $('#save-edit').disabled=true;
  try{const item=await api(`/recordings/${editing.mid}/segments/${encodeURIComponent(editing.sid)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:editing.revision,text:$('#edit-text').value,speaker:$('#edit-speaker').value||null})});$('#edit-dialog').close();if(current?.id===item.id){current=item;renderDetail();}}
  catch(e){toast(e.message);}finally{$('#save-edit').disabled=false;}
};
setInterval(async()=>{
  if(!current||current.summary_status!=='processing'||polling)return;
  polling=true;const id=current.id;
  try{const item=await api('/recordings/'+id);if(current?.id===id){current=item;renderSummary();}}
  catch(e){toast(e.message);}finally{polling=false;}
},2500);

let splitting=null;
async function openSplit(segment){
  const mid=current.id;
  const data=await api(`/recordings/${mid}/segments/${encodeURIComponent(segment.id)}/boundaries`);
  if(current?.id!==mid)return;
  splitting={mid,sid:segment.id,revision:data.revision,segment,boundaries:data.boundaries};
  $('#split-word').innerHTML=data.boundaries.map((b,i)=>`<option value="${i}">${escape(b.label)}</option>`).join('');
  const options='<option value="">Unassigned</option>'+Object.entries(current.speakers).map(([id,name])=>`<option value="${escape(id)}">${escape(name)}</option>`).join('');
  $('#split-left').innerHTML=options;$('#split-right').innerHTML=options;
  $('#split-left').value=segment.speaker??'';$('#split-right').value=segment.speaker??'';
  $('#split-listen').disabled=!current.has_audio;
  previewSplit();$('#split-dialog').showModal();
}
function previewSplit(){
  if(!splitting)return;
  const s=splitting.segment,b=splitting.boundaries[Number($('#split-word').value)];if(!b)return;
  $('#split-left-text').textContent=s.text.slice(0,b.offset).trim();
  $('#split-right-text').textContent=s.text.slice(b.offset).trim();
  $('#split-time').value=b.time??'';
  $('#split-timing-note').textContent=b.time==null?'No reliable word timestamp. Listen and enter the boundary time.':'Time suggested from ASR word timestamp; adjust it after listening if needed.';
}
$('#split-word').onchange=previewSplit;
$('#split-listen').onclick=async()=>{
  if(!splitting)return;
  const value=$('#split-time').value;
  $('#audio').currentTime=Math.max(splitting.segment.start,(value?Number(value):splitting.segment.start)-1);
  try{await $('#audio').play();}catch(e){toast(e.message);}
};
$('#split-cancel').onclick=()=>$('#split-dialog').close();
$('#split-form').onsubmit=async e=>{
  e.preventDefault();if(!splitting)return;$('#split-save').disabled=true;
  try{
    const b=splitting.boundaries[Number($('#split-word').value)];
    const result=await api(`/recordings/${splitting.mid}/segments/${encodeURIComponent(splitting.sid)}/split`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:splitting.revision,offset:b.offset,at:Number($('#split-time').value),left_speaker:$('#split-left').value||null,right_speaker:$('#split-right').value||null})});
    $('#split-dialog').close();if(current?.id===result.id){current=result;renderDetail();}
  }catch(error){toast(error.message);}finally{$('#split-save').disabled=false;}
};


const chatDrafts=new Map();
let chatRenderedKey=null,chatRecording=null,chatSending=false;
function renderQA(){
  if(!current)return;
  const busy=current.qa_status==='processing';
  const blocked=busy||chatSending||current.summary_status==='processing'||active(current.status)||!current.segments?.length;
  $('#qa-ask').disabled=blocked;
  $('#qa-provider').disabled=busy||chatSending;
  if(chatRecording!==current.id){
    chatRecording=current.id;
    $('#qa-question').value=chatDrafts.get(current.id)||'';
  }
  $('#qa-note').textContent=$('#qa-provider').value==='kaggle' ? 'Hỏi đáp bằng GPU đang chọn, có dẫn chứng từ transcript.' : $('#qa-provider').value==='api'
    ?'Recent chat and transcript excerpts are sent to your configured API provider.'
    :'Chat is saved for this recording. Recent questions help with follow-ups; answers are checked against the transcript. Editing the transcript clears this chat. Local CPU replies may take a few minutes.';
  $('#qa-status').textContent=current.qa_status==='error'?current.qa_error||'Reply failed. Please retry.':'';
  $('#qa-retry').hidden=current.qa_status!=='error'||!current.qa_pending;
  $('#qa-retry').disabled=blocked;
  const rows=(current.qa||[]).filter(q=>q.revision===current.revision);
  const key=JSON.stringify([current.id,current.revision,rows,current.qa_status,current.qa_pending]);
  if(key===chatRenderedKey)return;
  const container=$('#qa-results');
  const nearBottom=container.scrollHeight-container.scrollTop-container.clientHeight<100;
  const changedRecording=container.dataset.recording!==current.id;
  container.dataset.recording=current.id;chatRenderedKey=key;
  container.innerHTML=rows.map(q=>`<div class="chat-row user"><div class="chat-bubble"><small>${uiText('You','Bạn')}</small><p>${escape(q.question)}</p></div></div><div class="chat-row assistant"><div class="chat-bubble"><small>VoxMeet</small>${q.answerable?q.points.map(p=>`<p>${escape(p.text)}</p><details><summary>${uiText('Sources','Dẫn chứng')} · ${p.evidence.length}</summary>${p.evidence.map(ref=>{const source=current.segments.find(s=>s.id===ref.id);return `<blockquote>${source&&current.has_audio?`<button type="button" class="text-button" data-time="${Number(source.start)}">▶ ${clock(source.start)}</button>`:''} ${escape(source?current.speakers[source.speaker]||'Unassigned':'')}<br>${escape(ref.quote)}</blockquote>`;}).join('')}</details>`).join(''):`<p>${uiText('I could not find enough support in the selected transcript to answer confidently.','Chưa đủ thông tin trong transcript để trả lời chắc chắn.')}</p>`}${q.scope==='retrieved_excerpts'?`<small>${uiText('Based on selected transcript excerpts.','Dựa trên các đoạn transcript được chọn.')}</small>`:''}</div></div>`).join('');
  if(current.qa_pending){
    container.innerHTML+=`<div class="chat-row user"><div class="chat-bubble"><small>${uiText('You','Bạn')}</small><p>${escape(current.qa_pending)}</p></div></div>`;
    if(busy)container.innerHTML+='<div class="chat-row assistant"><div class="chat-bubble thinking"><small>VoxMeet</small><p>Reading the transcript…</p></div></div>';
  }
  if(!rows.length&&!current.qa_pending)container.textContent='';
  if(nearBottom||changedRecording||busy)container.scrollTop=container.scrollHeight;
}
$('#qa-provider').onchange=renderQA;
$('#qa-question').oninput=()=>{if(current)chatDrafts.set(current.id,$('#qa-question').value);};
$('#qa-question').onkeydown=event=>{
  if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();if(!$('#qa-ask').disabled)$('#qa-form').requestSubmit();}
};
$('#qa-retry').onclick=()=>{if(current?.qa_pending){$('#qa-question').value=current.qa_pending;$('#qa-form').requestSubmit();}};
$('#qa-form').onsubmit=async event=>{
  event.preventDefault();if(!current||chatSending)return;
  const id=current.id,question=$('#qa-question').value.trim();if(!question)return;
  chatSending=true;current.qa_pending=question;current.qa_status="processing";current.qa_error=null;renderQA();
  try{
    const item=await api(`/recordings/${id}/questions`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question,provider:$('#qa-provider').value})});
    if(current?.id===id){
      current=item;
      if($('#qa-question').value.trim()===question){$('#qa-question').value='';chatDrafts.set(id,'');}
      renderQA();$('#qa-question').focus();
    }
  }catch(e){if(current?.id===id){current.qa_status="error";current.qa_error=e.message;}toast(e.message);}finally{chatSending=false;if(current)renderQA();}
};
setInterval(async()=>{
  if(!current||current.qa_status!=='processing'||polling)return;
  polling=true;const id=current.id;
  try{const item=await api('/recordings/'+id);if(current?.id===id){current=item;renderQA();}}
  catch(e){toast(e.message);}finally{polling=false;}
},2500);

if(window.parent!==window){document.querySelector('a[href="/live"]')?.addEventListener('click',e=>{e.preventDefault();window.parent.postMessage({type:'voxmeet-show-live'},location.origin);});}

if(window.parent!==window){document.querySelector('a[href="/live"]')?.addEventListener('click',e=>{e.preventDefault();window.parent.postMessage({type:'voxmeet-show-live'},location.origin);});}

addEventListener('message',event=>{
 if(event.origin===location.origin&&event.source===window.parent&&event.data?.type==='voxmeet-refresh-recordings')refreshList().catch(error=>toast(error.message));
});

addEventListener('voxmeet-worker-status',event=>{
  if(caps)caps.community_worker=event.detail;
  if(current)refreshMode();
});

function notifyRecordingChange(id,deleted=false){
  const change={type:'voxmeet-recording-changed',id,deleted,at:Date.now()};
  localStorage.setItem('voxmeet-recording-change',JSON.stringify(change));
  if(deleted&&sessionStorage.getItem('voxmeet-live-id')===id)sessionStorage.removeItem('voxmeet-live-id');
  if(parent!==window)parent.postMessage(change,location.origin);
}
$('#rename-recording').onclick=async()=>{
 if(!current)return;const id=current.id;
 const title=await popup({title:uiText('Rename recording','Đổi tên bản ghi'),value:current.title});if(title===null)return;
 if(!title.trim()||title.trim().length>150){toast('Enter a name of 1–150 characters.');return;}
 try{const item=await api('/recordings/'+id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:title.trim()})});
 if(current?.id===id){current=item;renderDetail();}await refreshList();notifyRecordingChange(id);
 }catch(e){toast(e.message);}
};
$('#delete-recording').onclick=async()=>{
 if(!current)return;const id=current.id;
 if(!(await popup({title:uiText('Delete recording?','Xóa bản ghi?'),message:uiText('Delete audio, transcript, summaries and chat for ','Xóa audio, transcript, tóm tắt và chat của ')+current.title+'?',destructive:true})))return;
 try{await api('/recordings/'+id,{method:'DELETE'});notifyRecordingChange(id,true);if(current?.id===id)home();await refreshList();}
 catch(e){toast(e.message);}
};
addEventListener('storage',async e=>{if(e.key!=='voxmeet-recording-change'||!e.newValue)return;
 try{const c=JSON.parse(e.newValue);if(current?.id===c.id){if(c.deleted)home();else await openRecording(c.id);}await refreshList();}catch(e){}
});

$('#recordings').addEventListener('click', async event => {
 const button = event.target.closest('[data-delete]');
 if (!button) return;
 event.stopPropagation();
 const row = recordings.find(item => item.id === button.dataset.delete);
 if (!row || !(await popup({title:uiText('Delete recording?','Xóa bản ghi?'),message:row.title,destructive:true}))) return;
 button.disabled = true;
 try {
  await api('/recordings/' + row.id, {method:'DELETE'});
  notifyRecordingChange(row.id, true);
  await refreshList();
 } catch (error) { toast(error.message);button.disabled=false; }
});

window.addEventListener('voxmeet-language-change', renderList);

if(new URLSearchParams(location.search).get('setup')==='1')showSetup();

// Record-only translation UI. The original transcript is never replaced.
function renderTranslation(){
  if(!current)return;
  const panel=$('#translation-panel');if(!panel)return;
  const busy=current.translation_status==='processing';
  $('#translate-recording').disabled=busy||active(current.status)||!current.segments?.length;
  $('#translation-language').disabled=busy;
  $('#translate-recording').textContent=uiText('Translate','Dịch bản ghi');
  $('#translation-show-label').textContent=uiText('Show translation','Hiện bản dịch');
  const fresh=current.translation?.revision===current.revision?current.translation:null;
  const target=current.translation_target||fresh?.target_language;
  if(target)$('#translation-language').value=target;
  const progress=current.translation_progress;
  $('#translation-status').textContent=busy
    ?uiText('Translating on the selected GPU','Đang dịch bằng GPU đang chọn')+(progress?` · ${progress.done}/${progress.total}`:'…')
    :current.translation_error||(fresh?.partial?uiText('Partial translation saved. Translate again to finish.','Đã lưu bản dịch một phần. Dịch lại để hoàn tất.'):null)||(!fresh&&current.translation?uiText('Transcript changed. Translate again.','Transcript đã thay đổi. Hãy dịch lại.'):'');
  const link=$('#translation-export');link.hidden=!fresh;
  link.textContent=uiText('Export bilingual .txt','Xuất song ngữ .txt');
  link.href=`/api/recordings/${current.id}/translation/export`;
  document.querySelectorAll('#transcript .turn .turn-translation').forEach(el=>el.remove());
  if(!fresh||!$('#translation-show').checked)return;
  const byId=new Map(fresh.segments.map(s=>[s.id,s.text]));
  document.querySelectorAll('#transcript .turn').forEach((article,index)=>{
    const text=byId.get(current.segments[index]?.id);if(!text)return;
    const paragraph=document.createElement('p');paragraph.className='turn-translation';
    paragraph.lang=fresh.target_language;
    const label=document.createElement('small');
    label.textContent=fresh.target_language==='vi'?'Tiếng Việt':'English';
    paragraph.append(label,document.createElement('br'),document.createTextNode(text));
    article.lastElementChild.append(paragraph);
  });
}
$('#translation-show').onchange=renderTranslation;
$('#translate-recording').onclick=async()=>{
  if(!current)return;const id=current.id;
  const target=$('#translation-language').value;
  $('#translate-recording').disabled=true;
  try{
    const item=await api(`/recordings/${id}/translation`,{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({target_language:target})});
    if(current?.id===id){current=item;$('#translation-show').checked=true;renderTranslation();}
  }catch(error){toast(error.message);if(current?.id===id)renderTranslation();}
};
const renderBeforeTranslation=renderDetail;
renderDetail=function(){renderBeforeTranslation();renderTranslation();};
let pollingTranslation=false;
setInterval(async()=>{
  if(!current||current.translation_status!=='processing'||pollingTranslation)return;
  pollingTranslation=true;const id=current.id;
  try{const item=await api('/recordings/'+id);if(current?.id===id){current=item;renderTranslation();}}
  catch(error){toast(error.message);}finally{pollingTranslation=false;}
},2000);

const renderBeforeAcousticSpans=renderDetail;
renderDetail=function(){
  renderBeforeAcousticSpans();if(!current)return;
  document.querySelectorAll('#transcript .turn').forEach((article,index)=>{
    const turn=current.segments[index];if(!turn?.speaker_spans?.length)return;
    const body=article.querySelector('div');const original=body.querySelector('p');
    const box=document.createElement('div');box.className='acoustic-spans';
    for(const span of turn.speaker_spans){
      const label=document.createElement('small');label.textContent=clock(span.start)+' · '+(current.speakers[span.speaker]||uiText('Unassigned','Chưa xác định'));
      const text=document.createElement('p');text.textContent=span.text;box.append(label,text);
    }
    if(original)original.replaceWith(box);
    const speaker=body.querySelector('.speaker');if(speaker)speaker.hidden=true;
  });
};

$('#record-turn-policy').onchange=()=>{if(current)refreshMode();};
