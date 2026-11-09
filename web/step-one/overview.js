const t = (en, vi) => localStorage.getItem('voxmeet-ui-language') === 'en' ? en : vi;
async function refreshOverview() {
  try {
    const responses = await Promise.all([fetch('/api/recordings'), fetch('/api/community-worker')]);
    if (responses.some(response => !response.ok)) throw Error();
    const [recordings, worker] = await Promise.all(responses.map(response => response.json()));
    document.querySelector('#overview-count').textContent = recordings.length;
    document.querySelector('#overview-worker').textContent = worker.connected ? t('Online', 'Đang kết nối') : t('Offline', 'Chưa kết nối');
  } catch {
    document.querySelector('#overview-error').textContent = t('Could not load status.', 'Không tải được trạng thái.');
  }
}
refreshOverview();
window.addEventListener('voxmeet-language-change', refreshOverview);
