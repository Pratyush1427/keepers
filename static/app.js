async function api(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

let toastTimer;
function toast(message, isError = false) {
  const el = document.getElementById('toast');
  el.textContent = message;
  el.classList.toggle('error', isError);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3500);
}

// Show a message after the next page load (for actions that reload).
function toastAfterReload(message) {
  try { sessionStorage.setItem('toast', message); } catch (e) {}
}

// An in-page "are you sure?" bar. Resolves true/false.
function confirmBar(message, actionLabel) {
  return new Promise((resolve) => {
    document.querySelectorAll('.confirm').forEach((el) => el.remove());
    const bar = document.createElement('div');
    bar.className = 'confirm';
    bar.innerHTML = '<span></span><button class="btn ghost small" data-no>Cancel</button><button class="btn primary small" data-yes></button>';
    bar.querySelector('span').textContent = message;
    bar.querySelector('[data-yes]').textContent = actionLabel;
    const done = (answer) => { bar.remove(); document.removeEventListener('keydown', onKey, true); resolve(answer); };
    const onKey = (e) => {
      if (e.key === 'Escape') { e.stopPropagation(); done(false); }
      if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); done(true); }
    };
    bar.querySelector('[data-no]').onclick = () => done(false);
    bar.querySelector('[data-yes]').onclick = () => done(true);
    document.addEventListener('keydown', onKey, true);
    document.body.append(bar);
    bar.querySelector('[data-yes]').focus();
  });
}

// localStorage can be unavailable (private windows); preferences are a nicety.
const store = {
  get(key) { try { return localStorage.getItem('po.' + key); } catch (e) { return null; } },
  set(key, value) { try { localStorage.setItem('po.' + key, value); } catch (e) {} },
};

function isTyping(e) {
  return e.target.matches('input, select, textarea') || e.metaKey || e.ctrlKey || e.altKey;
}

// Lightroom-style culling keys -> the change to save, or null.
function cullKey(e) {
  if (/^[0-5]$/.test(e.key)) return { rating: Number(e.key) };
  switch (e.key.toLowerCase()) {
    case 'p': return { flag: 1 };
    case 'x': return { flag: -1 };
    case 'u': return { flag: 0 };
    default: return null;
  }
}

// Multi-select over elements with data-id: toggle, shift-click ranges, select all.
function createSelection(itemSelector, onChange) {
  const selected = new Set();
  let anchor = null;
  const items = () => [...document.querySelectorAll(itemSelector)];
  const sel = {
    selected,
    ids: () => [...selected].map(Number),
    render() {
      items().forEach((el) => el.classList.toggle('selected', selected.has(el.dataset.id)));
      document.body.classList.toggle('selecting', selected.size > 0);
      onChange(selected.size);
    },
    toggle(el) {
      const id = el.dataset.id;
      if (selected.has(id)) selected.delete(id); else selected.add(id);
      anchor = el;
      sel.render();
    },
    range(el) {
      const list = items();
      const from = anchor ? list.indexOf(anchor) : -1;
      if (from < 0) return sel.toggle(el);
      const to = list.indexOf(el);
      list.slice(Math.min(from, to), Math.max(from, to) + 1).forEach((x) => selected.add(x.dataset.id));
      anchor = el;
      sel.render();
    },
    add(els) { els.forEach((el) => selected.add(el.dataset.id)); sel.render(); },
    all() { sel.add(items()); },
    clear() { selected.clear(); anchor = null; sel.render(); },
  };
  return sel;
}

// Fade images in once loaded instead of popping.
function fadeInImages(root = document) {
  root.querySelectorAll('img.fade').forEach((img) => {
    if (img.complete && img.naturalWidth) img.classList.add('loaded');
    else img.addEventListener('load', () => img.classList.add('loaded'), { once: true });
  });
}

(() => {
  const help = document.getElementById('help');
  document.getElementById('help-open').addEventListener('click', () => help.showModal());
  document.addEventListener('keydown', (e) => {
    if (e.key === '?' && !isTyping(e) && !help.open) { e.preventDefault(); help.showModal(); }
  });

  document.getElementById('theme-toggle').addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
    document.documentElement.dataset.theme = next;
    store.set('theme', next);
  });

  try {
    const message = sessionStorage.getItem('toast');
    if (message) { sessionStorage.removeItem('toast'); toast(message); }
  } catch (e) {}
  fadeInImages();
})();
