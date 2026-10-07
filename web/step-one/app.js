const $ = selector => document.querySelector(selector);
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const clock = value => {const n=Math.floor(value||0);return `${String(Math.floor(n/60)).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`;};
const active = status => ['recording','queued','decoding','transcribing','cancelling'].includes(status);
const labels = {recording:'Recording live',queued:'Waiting',decoding:'Preparing audio',uploaded:'Ready to transcribe',transcribing:'Transcribing',cancelling:'Cancelling',cancelled:'Cancelled',ready:'Transcript ready',error:'Needs attention',interrupted:'Interrupted'};
const stages={loading_models:'Loading local speech models…',recognizing_speech:'Recognizing English speech',identifying_speakers:'Identifying speakers',aligning_words:'Matching words to speaker turns…',saving:'Saving your transcript…',requesting_api:'Waiting for the speech API…'};
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
  $('#recordings').innerHTML=rows.map(r=>`<button class="recording" data-id="${escape(r.id)}"><span class="recording-icon" aria-hidden="true">≋</span><div><strong>${escape(r.title)}</strong><small>${escape(new Date(r.created_at).toLocaleDateString('en',{month:'short',day:'numeric'}))} · ${clock(r.duration)} · English</small></div><span class="tag ${escape(r.status)}">${escape(labels[r.status]||r.status)}</span><span aria-hidden="true">↗</span></button>`).join('')||`<div class="empty"><span aria-hidden="true">≋</span><h3>${term?'No matching recordings.':'Your first conversation starts here.'}</h3><p>${term?'Try a different title.':'Upload an English audio file to get started.'}</p></div>`;
}
async function refreshList(){recordings=await api('/recordings');renderList();}
function refreshMode(){
  const provider=$('#provider').value;
  const ready=caps&&(provider==='local'?caps.local_ready:caps.deepgram_configured);
  $('#provider-note').textContent=provider==='local'?'Legacy CPU mode may split one person into several speakers. Use GPU transcript below for Community-1.':'Audio will be sent to Deepgram to transcribe and separate speakers. Provider charges may apply.';
  $('#transcribe').disabled=!current?.has_audio||active(current?.status)||!ready;
  $('#provider').disabled=active(current?.status);
  $('#speaker-count').disabled=active(current?.status)||provider!=='local';
  $('#transcribe').textContent=current?.status==='ready'?'Transcribe again →':'Create transcript →';
  if(current&&!active(current.status))$('#status').textContent=!ready?'Set up this processing mode before creating a transcript.':current.status==='ready'?'Transcript ready. Compare the words with the original audio.':'Audio prepared. Choose a mode and create your transcript.';
}
function renderDetail(){
  if(!current)return;
  const segments=current.segments||[], speakers=current.speakers||{};
  $('#breadcrumb').textContent='Recording';$('#recording-title').textContent=current.title;
  $('#recording-meta').textContent=`${current.filename||'Audio recording'} · ${clock(current.duration)} · English`;
  $('#player').hidden=!current.has_audio;
  const audioPath=`/api/recordings/${current.id}/audio`;
  if(current.has_audio&&$('#audio').getAttribute('src')!==audioPath)$('#audio').src=audioPath;
  const stageLabel=stages[current.stage]||'Processing your recording…';
  $('#status').textContent={queued:'Waiting for the current processing task…',decoding:'Preparing audio for playback and speech recognition…',transcribing:stageLabel+(current.progress!=null?` · ${current.progress}% of this stage`:''),cancelling:'Cancelling after the current model operation finishes…'}[current.status]||'';
  $('#progress').hidden=current.status!=='transcribing'||current.progress==null;
  $('#progress').value=current.progress||0;
  $('#cancel').hidden=!['queued','transcribing','cancelling'].includes(current.status)||current.stage==null;
  $('#cancel').disabled=current.status==='cancelling';
  $('#error').hidden=!current.error;
  $('#error').innerHTML=escape(current.error||'')+(!current.has_audio&&current.error?'<button id="retry-prepare" class="text-button">Retry preparation</button>':'');
  $('#warning').hidden=!current.warning;$('#warning').textContent=current.warning||'';
  $('#export').hidden=current.status!=='ready';$('#export').href=`/api/recordings/${current.id}/export`;
  $('#stats').textContent=`${Object.keys(speakers).length} speakers · ${segments.length} turns`;
  $('#speaker-legend').innerHTML=Object.entries(speakers).map(([id,name])=>`<span class="speaker-chip" style="background:${colours[Number(id)%colours.length]}">● ${escape(name)}</span>`).join('');
  $('#transcript').innerHTML=segments.map(s=>`<article class="turn" data-start="${Number(s.start)}" data-end="${Number(s.end)}"><button class="time" data-time="${Number(s.start)}" aria-label="Play from ${clock(s.start)}">${clock(s.start)}</button><div><span class="speaker" style="background:${colours[Number(s.speaker||0)%colours.length]}">${escape(speakers[s.speaker]||'Unassigned')}</span><p>${escape(s.text)}</p></div></article>`).join('')||`<div class="empty"><span aria-hidden="true">≋</span><h3>${active(current.status)?'Your recording is being processed.':current.status==='ready'?'No speech detected.':'A place for every voice.'}</h3><p>${active(current.status)?'The transcript will appear here when processing finishes.':current.status==='ready'?'Try a recording with clearer speech.':'Create a transcript to see speaker-labelled turns and timestamps.'}</p></div>`;
  refreshMode();
}
async function openRecording(id){
  const item=await api('/recordings/'+id);current=item;
  history.replaceState(null,'',`#recording=${encodeURIComponent(id)}`);
  $('#library').hidden=true;$('#detail').hidden=false;$('#provider').value=['local','deepgram'].includes(item.provider)?item.provider:'local';$('#speaker-count').value=item.num_speakers||'';renderDetail();
}
async function refreshCaps(){
  caps=await api('/health');
  const hasProvider=caps.deepgram_configured||caps.local_ready;
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
  uploading=true;$('#choose-file').disabled=true;$('#upload-status').hidden=false;$('#upload-status').textContent=`Uploading ${file.name}…`;
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
  try{const updated=await api(`/recordings/${id}/transcribe`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({provider:$('#provider').value,num_speakers:$('#provider').value==='local'&&$('#speaker-count').value?Number($('#speaker-count').value):null})});if(current?.id===id){current=updated;renderDetail();}}
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
  $('#summary-note').textContent=$('#summary-provider').value==='api'
    ?'Transcript text will be sent to your configured API provider. Charges may apply.'
    :`Uses Ollama (${caps?.ollama_model||'configured model'}) on this computer. Start Ollama first; CPU processing may take several minutes.`;
  $('#summary-status').textContent=busy?'Creating a draft from the current transcript…':current.summary_error||'';
  const summary=current.summary?.revision===current.revision?current.summary:null;
  $('#summary-export').hidden=!summary;
  $('#summary-export').href=`/api/recordings/${current.id}/summary/export`;
  $('#summary-result').innerHTML=summary?summary.sections.map(section=>`<ul>${section.bullets.map(b=>`<li>${escape(b.text)}<details><summary>Check source: ${escape(b.ids.join(', '))}</summary>${b.ids.map(id=>{const s=current.segments.find(s=>s.id===id);return s?`<blockquote>[${escape(id)}] ${clock(s.start)} · ${escape(current.speakers[s.speaker]||'Unassigned')}<br>${escape(s.text)}</blockquote>`:'';}).join('')}</details></li>`).join('')}</ul>`).join(''):'<p>No draft yet. Review or edit the transcript, then generate a summary.</p>';
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
  const unavailable=!current.has_audio||active(current.status)||current.summary_status==='processing';
  $('#gpu-package').hidden=unavailable;
  $('#gpu-package').href=`/api/recordings/${current.id}/gpu-package`;
  $('#gpu-import').disabled=unavailable;
  $('#gpu-connect').disabled=unavailable||gpuConnecting;
  const session=current.gpu_session;
  $('#gpu-connected-notebook').hidden=!session||session.status!=='waiting';
  $('#gpu-connected-notebook').href=`/api/recordings/${current.id}/connected-notebook`;
  $('#gpu-connection-status').textContent=gpuConnecting?'Opening transfer link…':session?.error||(session?.status==='waiting'?'Connection ready for one hour. Download this recording’s notebook, open it in Colab, enable T4 + HF_TOKEN and run it. The transcript will return here automatically.':session?.status==='applied'?'GPU transcript received and applied. Review it, then summarize.':'');
  $('#gpu-provenance').textContent=current.provenance?`Current transcript: ${current.provenance.asr_model} + ${current.provenance.diarization_model}`:'Community-1 has not been applied to this recording.';
};
$('#summary-provider').onchange=renderSummary;
$('#summarize').onclick=async()=>{
  if(!current)return;
  const id=current.id;$('#summarize').disabled=true;
  try{const item=await api(`/recordings/${id}/summary`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({provider:$('#summary-provider').value})});if(current?.id===id){current=item;renderSummary();}}
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

$('#gpu-import').onclick=()=>$('#gpu-file').click();
$('#gpu-file').onchange=async()=>{
  const file=$('#gpu-file').files[0];if(!file||!current)return;
  const id=current.id;$('#gpu-import').disabled=true;
  try{
    const form=new FormData();form.append('file',file);
    const result=await api(`/recordings/${id}/gpu-result`,{method:'POST',body:form});
    if(current?.id===id){current=result;renderDetail();}
    await refreshList();toast('GPU transcript applied. Previous transcript backed up.');
  }catch(e){toast(e.message);}finally{$('#gpu-file').value='';if(current)renderDetail();}
};

$('#gpu-connect').onclick=async()=>{
  if(!current||gpuConnecting)return;
  const id=current.id;gpuConnecting=true;renderDetail();
  try{
    const item=await api(`/recordings/${id}/gpu-session`,{method:'POST'});
    if(current?.id===id)current=item;
  }catch(e){toast(e.message);}finally{gpuConnecting=false;if(current)renderDetail();}
};
setInterval(async()=>{
  if(!current||current.gpu_session?.status!=='waiting'||polling||gpuConnecting)return;
  polling=true;const id=current.id;
  try{
    const item=await api(`/recordings/${id}/gpu-session/check`,{method:'POST'});
    if(current?.id===id){current=item;if(item.gpu_session?.status!=='waiting')renderDetail();}
  }catch(e){toast(e.message);}finally{polling=false;}
},4000);

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
  $('#qa-note').textContent=$('#qa-provider').value==='api'
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
  container.innerHTML=rows.map(q=>`<div class="chat-row user"><div class="chat-bubble"><small>You</small><p>${escape(q.question)}</p></div></div><div class="chat-row assistant"><div class="chat-bubble"><small>VoxMeet</small>${q.answerable?q.points.map(p=>`<p>${escape(p.text)}</p><details><summary>Sources · ${p.evidence.length}</summary>${p.evidence.map(ref=>{const source=current.segments.find(s=>s.id===ref.id);return `<blockquote>${source&&current.has_audio?`<button type="button" class="text-button" data-time="${Number(source.start)}">▶ ${clock(source.start)}</button>`:''} ${escape(source?current.speakers[source.speaker]||'Unassigned':'')}<br>${escape(ref.quote)}</blockquote>`;}).join('')}</details>`).join(''):'<p>I could not find enough support in the selected transcript to answer confidently.</p>'}${q.scope==='retrieved_excerpts'?'<small>Based on selected transcript excerpts.</small>':''}</div></div>`).join('');
  if(current.qa_pending){
    container.innerHTML+=`<div class="chat-row user"><div class="chat-bubble"><small>You</small><p>${escape(current.qa_pending)}</p></div></div>`;
    if(busy)container.innerHTML+='<div class="chat-row assistant"><div class="chat-bubble thinking"><small>VoxMeet</small><p>Reading the transcript…</p></div></div>';
  }
  if(!rows.length&&!current.qa_pending)container.innerHTML='<div class="chat-welcome"><strong>Talk through your recording</strong><p>Try “How long is the nap?” Then ask a follow-up without repeating the full question.</p></div>';
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
  chatSending=true;renderQA();
  try{
    const item=await api(`/recordings/${id}/questions`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question,provider:$('#qa-provider').value})});
    if(current?.id===id){
      current=item;
      if($('#qa-question').value.trim()===question){$('#qa-question').value='';chatDrafts.set(id,'');}
      renderQA();$('#qa-question').focus();
    }
  }catch(e){toast(e.message);}finally{chatSending=false;if(current)renderQA();}
};
setInterval(async()=>{
  if(!current||current.qa_status!=='processing'||polling)return;
  polling=true;const id=current.id;
  try{const item=await api('/recordings/'+id);if(current?.id===id){current=item;renderQA();}}
  catch(e){toast(e.message);}finally{polling=false;}
},2500);
