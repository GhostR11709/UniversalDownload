const quality = document.querySelector('#default-quality');
const saved = document.querySelector('#saved');
chrome.storage.local.get({ defaultQuality: 'best' }).then((value) => { quality.value = value.defaultQuality; });
document.querySelector('#settings-form').addEventListener('submit', async (event) => { event.preventDefault(); await chrome.storage.local.set({ defaultQuality: quality.value }); saved.classList.add('show'); setTimeout(() => saved.classList.remove('show'), 1800); });
