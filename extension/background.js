const API = 'http://127.0.0.1:8756';

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) throw new Error(data.hint || data.error || `Server returned ${response.status}`);
  return data;
}

async function waitForJob(jobId) {
  for (let attempt = 0; attempt < 3600; attempt += 1) {
    const job = await request(`/api/jobs/${encodeURIComponent(jobId)}`);
    if (['done', 'error', 'cancelled'].includes(job.state)) return job;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error('The download took too long to finish. Check the dashboard.');
}

async function downloadInBackground(url, pageUrl = '', title = '') {
  const queued = await request('/api/downloads', { method: 'POST', body: JSON.stringify({ url, page_url: pageUrl, title }) });
  const job = await waitForJob(queued.job.id);
  if (job.state !== 'done') throw new Error(job.error || 'Download failed.');
  const downloadId = await chrome.downloads.download({ url: `${API}/files/${encodeURIComponent(job.id)}`, filename: job.filename || undefined, saveAs: false });
  chrome.notifications.create(`ghostr-${job.id}`, { type: 'basic', iconUrl: 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="16" fill="%237c5cff"/><path fill="white" d="M16 30h24l-8-8 4-4 16 16-16 16-4-4 8-8H16z"/></svg>', title: 'GhostR download ready', message: job.title || 'The file is downloading in Chrome.' });
  return { ...job, downloadId };
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({ id: 'download-link', title: 'Download with GhostR', contexts: ['link', 'page', 'video', 'audio'], targetUrlPatterns: ['http://*/*', 'https://*/*'] });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const url = info.linkUrl || info.srcUrl || info.pageUrl || tab?.url;
  if (!url) return;
  try { await downloadInBackground(url, info.pageUrl || tab?.url || '', tab?.title || ''); }
  catch (error) { chrome.notifications.create(`ghostr-error-${Date.now()}`, { type: 'basic', iconUrl: 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="16" fill="%23ef476f"/><path fill="white" d="M32 14 10 52h44L32 14zm0 12 3 14h-6l3-14zm0 20a3 3 0 1 0 0-6 3 3 0 0 0 0 6z"/></svg>', title: 'GhostR could not download that', message: error.message }); }
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== 'download-context') return false;
  downloadInBackground(message.url, message.pageUrl, message.title).then((job) => sendResponse({ ok: true, job })).catch((error) => sendResponse({ ok: false, error: error.message }));
  return true;
});
