import { popup } from './popup.js';
// Shared by the recording page and persistent Live workspace.
const status = document.querySelector('#community-status');
const button = document.querySelector('#community-connect');
const notebook = document.querySelector('#community-notebook');
let busy = false;
async function refresh() {
  if (busy) return;
  try {
    const response = await fetch('/api/community-worker', {cache:'no-store'});
    if (!response.ok) throw Error('Could not check the worker connection.');
    const state = await response.json();
    status.textContent = state.connected ? (state.features?.includes('multilingual')?'Worker đã kết nối · Việt/English, Live, tóm tắt và chat. Kiểm tra phiên bản nhận diện trong log Kaggle.':'Worker cũ online — tải notebook mới để dùng tiếng Việt và LLM.') :
      state.configured ? (localStorage.getItem('voxmeet-ui-language')==='en'?'Existing session is valid, but the worker has stopped responding. Check the current Kaggle cell and rerun it if stopped.':'Phiên hiện tại còn hiệu lực nhưng worker mất phản hồi. Kiểm tra cell Kaggle hiện tại; nếu cell đã dừng, chạy lại cell đó.') :
      'Worker offline. Connect once, then start the session notebook in Kaggle.';
    notebook.hidden = !state.configured;
    button.textContent = state.configured ? (localStorage.getItem('voxmeet-ui-language')==='en'?'Create replacement session':'Tạo phiên kết nối thay thế') : (localStorage.getItem('voxmeet-ui-language')==='en'?'Connect Kaggle worker':'Kết nối worker Kaggle');
    window.dispatchEvent(new CustomEvent('voxmeet-worker-status', {detail:state}));
  } catch (error) { status.textContent = error.message; }
}
button.onclick = async () => {
  if (!(await popup({title:localStorage.getItem('voxmeet-ui-language')==='en'?'Connect Kaggle worker?':'Kết nối worker Kaggle?',message:localStorage.getItem('voxmeet-ui-language')==='en'?'This replaces the existing worker connection. Submitted audio and transcript are sent to Kaggle through Cloudflare.':'Kết nối này thay thế worker hiện tại. Audio và transcript gửi xử lý sẽ được chuyển tới Kaggle qua Cloudflare.'}))) return;
  busy = true; button.disabled = true;
  status.textContent = 'Opening connection…';
  try {
    const response = await fetch('/api/community-worker/connect', {method:'POST'});
    const result = await response.json();
    if (!response.ok) throw Error(typeof result.detail === 'string' ? result.detail : 'Could not connect.');
    busy = false;
    await refresh();
  } catch (error) { status.textContent = error.message; }
  finally { busy = false; button.disabled = false; }
};
document.querySelector('#setup-dialog').addEventListener('toggle', () => {
  if (document.querySelector('#setup-dialog').open) refresh();
});
window.addEventListener('voxmeet-refresh-worker', refresh);
document.querySelector('#refresh-setup').addEventListener('click', refresh);
setInterval(refresh, 5000);
refresh();
