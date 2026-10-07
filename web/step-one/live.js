const $=s=>document.querySelector(s);
let socket,context,worklet,streams=[],nodes=[],active=false,stopping=false,sent=0,ack=0,turns=new Map(),savedId=null;
const fail=message=>{$('#error').hidden=false;$('#error').textContent=message;};
async function cleanup(){active=false;for(const s of streams)for(const t of s.getTracks())t.stop();streams=[];for(const n of nodes)n.disconnect();nodes=[];if(context){const old=context;context=null;old.close().catch(()=>{});}active=false;$('#stop').disabled=true;$('#mic').disabled=false;$('#system').disabled=false;$('#start').disabled=socket?.readyState===0||socket?.readyState===1;}
function render(turn){turns.set(turn.id,turn);const list=[...turns.values()].sort((a,b)=>a.start-b.start);$('#turns').replaceChildren(...list.map(t=>{const el=document.createElement('div');el.className='live-turn'+(t.committed?'':' pending');const label=document.createElement('small');label.textContent=`${Math.floor(t.start/60)}:${String(Math.floor(t.start%60)).padStart(2,'0')} · ${t.source_channel===0?'Room microphone':'Meeting audio'} · ${t.committed?'Draft turn':'Listening…'}`;const p=document.createElement('p');p.textContent=t.text;el.append(label,p);return el;}));}
async function stop(){if(!active||stopping)return;stopping=true;$('#stop').disabled=true;$('#state').textContent='Saving received audio and transcript…';if(worklet)worklet.port.postMessage('stop');else if(socket?.readyState===1)socket.send(JSON.stringify({type:'stop'}));}
$('#stop').onclick=stop;
$('#start').onclick=async()=>{
  $('#error').hidden=true;$('#saved').hidden=true;$('#start').disabled=true;turns.clear();$('#turns').replaceChildren();savedId=null;sent=0;ack=0;stopping=false;worklet=null;
  const enabled=[$('#mic').checked,$('#system').checked];
  if(!enabled.some(Boolean)){fail('Choose at least one source.');$('#start').disabled=false;return;}
  $('#mic').disabled=true;$('#system').disabled=true;
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
    socket.onopen=()=>socket.send(JSON.stringify({sources:enabled}));
    socket.onmessage=async event=>{
      const message=JSON.parse(event.data);
      if(message.type==='loading')$('#state').textContent='Loading local streaming model…';
      if(message.type==='ready'){
        savedId=message.id;
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
        $('#state').textContent='Recording locally · live transcript is provisional';$('#stop').disabled=false;
      }
      if(message.type==='update'){ack=message.frames;message.turns.forEach(render);$('#timing').textContent=`Received ${(ack/16000).toFixed(1)}s · queued audio ${Math.max(0,(sent-ack)/16000).toFixed(1)}s · last processing ${message.processing_ms}ms (not word latency)`;}
      if(message.type==='error'){fail(message.message);stop();}
      if(message.type==='saved'){savedId=message.id;$('#state').textContent=`Saved ${(message.duration/60).toFixed(1)} minutes. Review the draft or process with Colab.`;$('#saved').href=`/#recording=${message.id}`;$('#saved').hidden=false;await cleanup();}
    };
    socket.onerror=()=>fail('Live connection failed. Check that the demo is running.');
    socket.onclose=async()=>{if(!stopping&&active)fail('Connection ended. Check Recordings for the received audio.');await cleanup();};
    for(const s of streams)for(const track of s.getTracks())track.onended=()=>{if(active&&!stopping){fail('An audio share or microphone ended. Saving this meeting.');stop();}};
  }catch(e){fail(e.message);await cleanup();}
};
addEventListener('beforeunload',e=>{if(active){e.preventDefault();e.returnValue='';}});
fetch('/api/live/status').then(r=>r.json()).then(s=>{$('#state').textContent=s.ready?'Ready · Streaming English model installed.':'Streaming model is not installed. Run setup_live.py first.';$('#start').disabled=!s.ready;}).catch(()=>fail('Could not reach the demo.'));
