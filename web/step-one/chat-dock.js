const live = !!document.querySelector('#live-panel');
const owner = document.querySelector(live ? '#live-panel' : '#detail');
let panel = document.querySelector('#qa-panel');
if (live) {
  panel = document.createElement('section');
  panel.id = 'live-chat-dock';
  const assistant = document.querySelector('#live-assistant');
  const heading = assistant.querySelector('h3');
  for (const node of [heading, ...Array.from(assistant.children).slice(Array.from(assistant.children).indexOf(heading)+1)]) panel.append(node);
}
if (owner && panel) {
  const key = 'voxmeet-chat-dock-open';
  let open = localStorage.getItem(key) !== '0';
  panel.classList.add('chat-dock');
  panel.setAttribute('aria-label', 'Meeting chat');
  const bar = document.createElement('div');bar.className='dock-bar';
  const title = document.createElement('strong');title.textContent='Meeting chat';
  const close = document.createElement('button');close.type='button';close.textContent='Close ×';close.setAttribute('aria-label','Close chat');
  bar.append(title,close);panel.prepend(bar);
  const toggle=document.createElement('button');toggle.type='button';toggle.className='dock-toggle button';toggle.textContent='Chat';toggle.setAttribute('aria-controls',panel.id);toggle.setAttribute('aria-label','Open meeting chat');
  document.body.append(panel,toggle);
  function sync(){
    const available=!owner.hidden;
    panel.hidden=!available||!open;toggle.hidden=!available||open;
    toggle.setAttribute('aria-expanded',String(available&&open));
    document.body.classList.toggle('chat-dock-open',available&&open);
  }
  close.onclick=()=>{open=false;localStorage.setItem(key,'0');sync();toggle.focus();};
  toggle.onclick=()=>{open=true;localStorage.setItem(key,'1');sync();panel.querySelector('textarea')?.focus();};
  panel.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();close.click();}});
  new MutationObserver(sync).observe(owner,{attributes:true,attributeFilter:['hidden']});
  sync();
}

if (!document.documentElement.classList.contains('embedded-workspace')) {
  const sidebar = document.querySelector('.sidebar');
  if (sidebar) {
    sidebar.id = 'workspace-menu';
    const bar = document.createElement('div');
    bar.className = 'workspace-topbar';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'menu-toggle';
    button.textContent = '☰';
    button.setAttribute('aria-controls', sidebar.id);
    const title = document.createElement('strong');
    const overlay = document.createElement('button');
    overlay.type = 'button';
    overlay.className = 'menu-overlay';
    let opened = false;
    const syncMenu = () => {
      const english = localStorage.getItem('voxmeet-ui-language') === 'en';
      document.body.classList.toggle('menu-collapsed', !opened);
      button.setAttribute('aria-expanded', String(opened));
      button.setAttribute('aria-label', english ? 'Toggle menu' : 'Đóng/mở menu');
      overlay.setAttribute('aria-label', english ? 'Close menu' : 'Đóng menu');
      overlay.hidden = !opened;
      title.textContent = live ? (english ? 'Live meeting' : 'Họp trực tiếp') : (english ? 'Recordings' : 'Bản ghi');
    };
    button.onclick = () => { opened = !opened; syncMenu(); };
    overlay.onclick = () => { opened = false; syncMenu(); button.focus(); };
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && opened && !document.querySelector('dialog[open]')) overlay.click();
    });
    const overview = document.createElement('a');
    overview.className = 'nav';
    overview.href = '/';
    overview.textContent = '⌂ ' + (localStorage.getItem('voxmeet-ui-language') === 'en' ? 'Overview' : 'Tổng quan');
    sidebar.querySelector('.sidebar-caption')?.after(overview);
    bar.append(button, title);
    document.body.append(bar, overlay);
    window.addEventListener('voxmeet-language-change', syncMenu);
    syncMenu();
  }
}
