import { popup } from './popup.js';
const status=document.querySelector('#community-status');
const button=document.querySelector('#community-connect');
const notebook=document.querySelector('#community-notebook');
const text=(en,vi)=>localStorage.getItem('voxmeet-ui-language')==='en'?en:vi;
let busy=false,state=null;
function render(){
  if(!state)return;
  const preparing=state.connected&&state.startup_state!=='error'&&(state.startup_state==='warming'||!(state.features||[]).includes('live_asr'));
  if(preparing)status.textContent=text('Preparing speech and chat models on Kaggle. The first start may take a few minutes.','Kaggle đang tải/nạp model nhận dạng và chat. Lần khởi động đầu có thể mất vài phút.');
  else if(state.connected&&state.startup_state==='error')status.textContent=text('Model preparation failed. Check the Kaggle output.','Chuẩn bị model thất bại. Kiểm tra output Kaggle.');
  else if(state.connected&&state.assistant_ready)status.textContent=text('Ready: speech and chat models are loaded.','Sẵn sàng: model nhận dạng và chat đã được nạp.');
  else if(state.connected&&state.gpu_count===1)status.textContent=text('One GPU: speech and chat share resources; the first question may load the chat model.','Chỉ có 1 GPU: nhận dạng và chat dùng chung tài nguyên; câu hỏi đầu có thể phải nạp model.');
  else status.textContent=state.connected?text('Kaggle is online. Chat preload is not confirmed for this worker version.','Kaggle đang online. Phiên bản worker này chưa xác nhận đã nạp sẵn model chat.'):text('Start the Kaggle worker cell after downloading the notebook.','Tải notebook rồi chạy cell worker Kaggle.');
  button.textContent=state.configured?text('Recreate Kaggle session','Tạo lại phiên Kaggle'):text('Connect Kaggle worker','Kết nối worker Kaggle');
  button.disabled=busy;notebook.hidden=!state.configured;
  notebook.href='/api/community-worker/notebook';notebook.textContent=text('Download Kaggle notebook','Tải notebook Kaggle');
}
async function refresh(){
  if(busy)return;
  try{
    const response=await fetch('/api/community-worker',{cache:'no-store'});
    if(!response.ok)throw Error(text('Cannot check worker status.','Không kiểm tra được kết nối.'));
    state=await response.json();render();window.dispatchEvent(new CustomEvent('voxmeet-worker-status',{detail:state}));
  }catch(error){status.textContent=error.message;}
}
button.onclick=async()=>{
  if(state?.configured&&!(await popup({title:text('Recreate Kaggle session?','Tạo lại phiên Kaggle?'),message:text('The existing worker will need the new session notebook.','Worker hiện tại cần chạy notebook của phiên kết nối mới.')})))return;
  busy=true;render();let failure=null;
  try{
    const response=await fetch('/api/community-worker/connect'+(state?.configured?'?replace=true':''),{method:'POST'});
    const result=await response.json();if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Connection failed');
    state=result;window.dispatchEvent(new CustomEvent('voxmeet-worker-status',{detail:state}));
  }catch(error){failure=error.message;}
  finally{busy=false;render();if(failure)status.textContent=failure;}
};
document.querySelector('#setup-dialog').addEventListener('toggle',()=>{if(document.querySelector('#setup-dialog').open)refresh();});
window.addEventListener('voxmeet-refresh-worker',refresh);
window.addEventListener('voxmeet-language-change',render);
document.querySelector('#refresh-setup').addEventListener('click',refresh);
// Skip background checks while this page/tab is not visible.
setInterval(()=>{if(document.hidden||(window.frameElement!=null&&window.frameElement.offsetParent===null))return;refresh();},5000);refresh();
