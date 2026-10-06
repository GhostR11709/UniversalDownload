const API = 'http://127.0.0.1:8756';
const WINDOW_KEY = 'windowBounds';
const LAST_TAB_KEY = 'lastMediaTab';
const DEFAULT_BOUNDS = { width: 940, height: 900 };
let downloaderWindowId = null;
const watchedJobs = new Set();
let lastMediaTab = null;

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) }
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) throw new Error(data.hint || data.error || `Server returned ${response.status}`);
  return data;
}

function icon(kind) {
  const bg = kind === 'error' ? '%23ffffff' : '%23000000';
  const fg = kind === 'error' ? '%23000000' : '%23ffffff';
  const glyph = kind === 'error'
    ? 'M32 12 12 52h40L32 12zm0 14 3.5 16h-7L32 26zm0 22a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z'
    : 'M16 30h24l-8-8 4-4 16 16-16 16-4-4 8-8H16z';
  return `data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="${bg}"/><path fill="${fg}" d="${glyph}"/></svg>`;
}

function notify(id, kind, title, message) {
  chrome.notifications.create(id, { type: 'basic', iconUrl: icon(kind), title, message }).catch(() => {});
}

// --------------------------------------------------------------------------- //
// Which page is the user actually looking at?
// The downloader runs in its own window, so chrome.tabs.query({currentWindow:true})
// would only ever find our own popup. Instead we remember the last real web tab
// the user touched and hand that back to the popup.
// --------------------------------------------------------------------------- //
function isWebTab(tab) {
  return Boolean(tab && /^https?:/i.test(tab.url || ''));
}

async function rememberTab(tab) {
  if (!isWebTab(tab)) return;
  lastMediaTab = { id: tab.id, url: tab.url, title: tab.title || '' };
  try { await chrome.storage.session.set({ [LAST_TAB_KEY]: lastMediaTab }); } catch { /* older Chrome */ }
}

async function rememberActiveTabIn(windowId) {
  if (windowId === downloaderWindowId || windowId === chrome.windows.WINDOW_ID_NONE) return;
  try {
    const [tab] = await chrome.tabs.query({ active: true, windowId });
    await rememberTab(tab);
  } catch { /* window vanished */ }
}

async function currentMediaTab() {
  if (isWebTab(lastMediaTab)) return lastMediaTab;
  try {
    const stored = await chrome.storage.session.get(LAST_TAB_KEY);
    const saved = stored[LAST_TAB_KEY];
    if (isWebTab(saved)) { lastMediaTab = saved; return saved; }
  } catch { /* older Chrome */ }
  for (const query of [{ active: true, lastFocusedWindow: true }, { active: true, currentWindow: true }]) {
    try {
      const [tab] = await chrome.tabs.query(query);
      if (isWebTab(tab)) { await rememberTab(tab); return lastMediaTab; }
    } catch { /* try the next strategy */ }
  }
  return null;
}

// --------------------------------------------------------------------------- //
// The downloader is a real Chrome window: drag it anywhere, resize it, and it
// reopens at the same size and position next time.
// --------------------------------------------------------------------------- //
async function openDownloaderWindow() {
  // Capture the page the user was on before this window steals focus.
  await captureFocusedTab();
  if (downloaderWindowId !== null) {
    try {
      const existing = await chrome.windows.get(downloaderWindowId);
      if (existing) {
        await chrome.windows.update(downloaderWindowId, { focused: true, drawAttention: true });
        return;
      }
    } catch {
      downloaderWindowId = null;
    }
  }
  const stored = await chrome.storage.local.get(WINDOW_KEY);
  const saved = stored[WINDOW_KEY] || {};
  const bounds = {
    width: clamp(saved.width, 640, 4000, DEFAULT_BOUNDS.width),
    height: clamp(saved.height, 520, 4000, DEFAULT_BOUNDS.height),
  };
  if (Number.isInteger(saved.left) && Number.isInteger(saved.top)) {
    bounds.left = saved.left;
    bounds.top = saved.top;
  }
  const page = popupUrl();
  try {
    const created = await chrome.windows.create({ url: page, type: 'popup', ...bounds, focused: true });
    downloaderWindowId = created.id;
  } catch {
    const created = await chrome.windows.create({ url: page, type: 'popup', focused: true });
    downloaderWindowId = created.id;
  }
}

// Hand the remembered page to the popup as ?url= so it can pre-fill instantly.
function popupUrl() {
  const page = chrome.runtime.getURL('popup.html');
  return isWebTab(lastMediaTab) ? `${page}?url=${encodeURIComponent(lastMediaTab.url)}` : page;
}

async function captureFocusedTab() {
  try {
    const win = await chrome.windows.getLastFocused({ populate: true });
    if (!win || win.id === downloaderWindowId || win.id === chrome.windows.WINDOW_ID_NONE) return;
    const tab = (win.tabs || []).find((item) => item.active) || null;
    if (isWebTab(tab)) await rememberTab(tab);
  } catch { /* no browser window focused */ }
}

function clamp(value, min, max, fallback) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return fallback;
  return Math.min(max, Math.max(min, Math.round(number)));
}

// --------------------------------------------------------------------------- //
// Jobs: the local server writes the file straight into the chosen folder, so
// nothing here touches chrome.downloads -> no save dialog, no shelf popup.
// --------------------------------------------------------------------------- //
async function waitForJob(jobId) {
  for (let attempt = 0; attempt < 3600; attempt += 1) {
    const job = await request(`/api/jobs/${encodeURIComponent(jobId)}`);
    if (['done', 'error', 'cancelled'].includes(job.state)) return job;
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  throw new Error('The download took too long to finish. Check the queue.');
}

async function watchJob(jobId) {
  if (watchedJobs.has(jobId)) return;
  watchedJobs.add(jobId);
  try {
    const job = await waitForJob(jobId);
    if (job.state === 'done') {
      notify(`ud-${jobId}`, 'done', 'Download finished', `${job.filename}\n${job.saved_path || job.folder || ''}`.trim());
    } else if (job.state === 'error') {
      notify(`ud-err-${jobId}-${Date.now()}`, 'error', 'Download failed', [job.error, job.hint].filter(Boolean).join(' — '));
    }
    chrome.runtime.sendMessage({ type: 'job-finished', jobId, job }).catch(() => {});
  } catch (error) {
    notify(`ud-err-${jobId}-${Date.now()}`, 'error', 'Download failed', error.message);
  } finally {
    watchedJobs.delete(jobId);
  }
}

async function queueFromContext(message) {
  const prefs = await chrome.storage.local.get({ folder: '', metadata: true, quality: 'best' });
  const queued = await request('/api/downloads', {
    method: 'POST',
    body: JSON.stringify({
      url: message.url,
      page_url: message.pageUrl || '',
      title: message.title || '',
      quality: message.quality || prefs.quality || 'best',
      filename: message.filename || '',
      folder: message.folder || prefs.folder || '',
      metadata: prefs.metadata !== false,
    }),
  });
  watchJob(queued.job.id);
  return queued.job;
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === 'watch-job') {
    watchJob(message.jobId);
    sendResponse({ ok: true });
    return false;
  }
  if (message?.type === 'get-active-tab') {
    currentMediaTab()
      .then((tab) => sendResponse({ ok: Boolean(tab), tab }))
      .catch(() => sendResponse({ ok: false, tab: null }));
    return true;
  }
  if (message?.type === 'reveal' || message?.type === 'open') {
    const endpoint = message.type === 'reveal' ? '/api/reveal' : '/api/open';
    request(endpoint, { method: 'POST', body: JSON.stringify({ id: message.jobId }) })
      .then(() => sendResponse({ ok: true }))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }
  if (message?.type !== 'download-context') return false;
  queueFromContext(message)
    .then((job) => sendResponse({ ok: true, job }))
    .catch((error) => sendResponse({ ok: false, error: error.message }));
  return true;
});

// --------------------------------------------------------------------------- //
// Entry points
// --------------------------------------------------------------------------- //
chrome.action.onClicked.addListener(() => { openDownloaderWindow().catch(() => {}); });
chrome.windows.onRemoved.addListener((windowId) => { if (windowId === downloaderWindowId) downloaderWindowId = null; });

// Track the page the user is really looking at, ignoring our own window.
chrome.windows.onFocusChanged.addListener((windowId) => { rememberActiveTabIn(windowId).catch(() => {}); });
chrome.tabs.onActivated.addListener(({ tabId, windowId }) => {
  if (windowId === downloaderWindowId) return;
  chrome.tabs.get(tabId).then(rememberTab).catch(() => {});
});
chrome.tabs.onUpdated.addListener((_tabId, info, tab) => {
  // Only the tab the user is looking at counts; background tabs finish loading too.
  if (!tab.active) return;
  if (info.status === 'complete' || info.url) rememberTab(tab).catch(() => {});
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: 'download-link', title: 'Download', contexts: ['link', 'page', 'video', 'audio'], targetUrlPatterns: ['http://*/*', 'https://*/*'] });
    chrome.contextMenus.create({ id: 'download-open-window', title: 'Open downloader', contexts: ['action'] });
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId === 'download-open-window') {
    openDownloaderWindow().catch(() => {});
    return;
  }
  const url = info.linkUrl || info.srcUrl || info.pageUrl || tab?.url;
  if (!url) return;
  try { await queueFromContext({ url, pageUrl: info.pageUrl || tab?.url || '', title: tab?.title || '' }); }
  catch (error) { notify(`ud-err-${Date.now()}`, 'error', 'Could not queue that link', error.message); }
});

// The popup page reports its own bounds; this is the safety net for windows
// that move without the page knowing (display changes, focus, etc).
chrome.windows.onBoundsChanged.addListener(async () => {
  if (downloaderWindowId === null) return;
  try {
    const win = await chrome.windows.get(downloaderWindowId);
    if (!win || typeof win.left !== 'number') return;
    await chrome.storage.local.set({ [WINDOW_KEY]: { left: win.left, top: win.top, width: win.width, height: win.height } });
  } catch { /* window closed mid-flight */ }
});
