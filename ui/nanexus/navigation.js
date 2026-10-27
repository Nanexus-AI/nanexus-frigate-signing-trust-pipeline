/* Nanexus-owned navigation. The sidebar N button opens a flyout only.
   Live Monitor and Historical Verification mount through the existing
   window.__nanexusLive and window.__nanexusHistorical entry points.
   Choosing the feature that is already showing does not mount it again. */
(() => {
'use strict';
if (window.__nanexusNavigation) return;
const timers = [];
let historicalCleanup = null;
let liveWait = null;
let current = null;
let liveGeneration = 0;
function clearLiveWait() {
  if (liveWait) {
    clearTimeout(liveWait);
    liveWait = null;
  }
}
const host = document.createElement('div');
host.id = 'nanexus-dashboard-host';
const el = (tag, text) => {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  return node;
};
const entry = el('button');
entry.type = 'button';
entry.id = 'nanexus-dashboard-open';
entry.hidden = true;
const mark = el('span', 'N');
mark.className = 'nx-entry-mark';
entry.append(mark);
entry.setAttribute('aria-label', 'Open Nanexus');
entry.setAttribute('aria-expanded', 'false');
entry.setAttribute('aria-haspopup', 'menu');
entry.setAttribute('aria-controls', 'nanexus-flyout');

const flyout = el('div');
flyout.id = 'nanexus-flyout';
flyout.hidden = true;
flyout.setAttribute('role', 'menu');
flyout.setAttribute('aria-label', 'Nanexus');
function menuItem(name, iconClass) {
  const button = el('button');
  button.type = 'button';
  button.setAttribute('role', 'menuitem');
  const icon = el('span');
  icon.className = 'nx-flyout-icon ' + iconClass;
  icon.setAttribute('aria-hidden', 'true');
  button.append(icon, document.createTextNode(name));
  button.addEventListener('click', () => choose(name));
  return button;
}
flyout.append(
  menuItem('Live Monitor', 'nx-icon-live'),
  menuItem('Historical Verification', 'nx-icon-history'),
);

const pane = el('section');
pane.id = 'nanexus-dashboard';
pane.hidden = true;
pane.setAttribute('aria-label', 'Nanexus');
const header = el('div');
header.id = 'nanexus-dashboard-header';
const title = el('h1');
title.tabIndex = -1;
const content = el('div');
header.append(title);
pane.append(header, content);
host.append(entry, flyout, pane);
document.body.append(host);

let railLayout = null;
let railCluster = null;
const railObserver = new ResizeObserver(() => positionEntry());
function positionEntry() {
  const rail = document.querySelector('aside');
  if (!rail) return;
  const links = [...rail.querySelectorAll('a[href]')].filter((node) => node.getClientRects().length);
  if (!links.length) return;
  const bounds = rail.getBoundingClientRect();
  const bottom = Math.max(...links.map((node) => node.getBoundingClientRect().bottom));
  const top = bottom + 16;
  entry.hidden = top + 34 > bounds.bottom;
  entry.style.left = `${bounds.left + (bounds.width - 34) / 2}px`;
  entry.style.top = `${top}px`;
  if (!flyout.hidden) positionFlyout();
  let cluster = links[0].parentElement;
  while (cluster && !links.every((node) => cluster.contains(node))) cluster = cluster.parentElement;
  if (rail !== railLayout || cluster !== railCluster) {
    railObserver.disconnect();
    railObserver.observe(rail);
    if (cluster && cluster !== rail) railObserver.observe(cluster);
    railLayout = rail;
    railCluster = cluster;
  }
}
function positionFlyout() {
  const rect = entry.getBoundingClientRect();
  const width = Math.min(280, Math.max(220, flyout.offsetWidth || 240));
  const height = flyout.offsetHeight || 92;
  let left = rect.right + 8;
  let top = rect.top;
  if (left + width > window.innerWidth - 8) left = Math.max(8, window.innerWidth - width - 8);
  if (top + height > window.innerHeight - 8) top = Math.max(8, window.innerHeight - height - 8);
  flyout.style.left = `${left}px`;
  flyout.style.top = `${top}px`;
}
positionEntry();
window.addEventListener('load', positionEntry, { once: true });
window.addEventListener('resize', positionEntry);
for (const delay of [100, 400, 1000]) timers.push(setTimeout(positionEntry, delay));

function closeFlyout() {
  flyout.hidden = true;
  entry.setAttribute('aria-expanded', 'false');
}
function openFlyout() {
  flyout.hidden = false;
  positionFlyout();
  entry.setAttribute('aria-expanded', 'true');
  const first = flyout.querySelector('button');
  if (first) first.focus();
}
function toggleFlyout() {
  if (flyout.hidden) openFlyout();
  else closeFlyout();
}
function releaseCurrent() {
  clearLiveWait();
  liveGeneration += 1;
  if (historicalCleanup) {
    historicalCleanup();
    historicalCleanup = null;
  }
  current = null;
  pane.classList.remove('nx-live');
  content.replaceChildren();
  title.textContent = '';
}
function leave(focus = true) {
  closeFlyout();
  releaseCurrent();
  pane.hidden = true;
  if (focus) entry.focus();
}
function unavailable(message) {
  const note = el('p', message);
  note.setAttribute('role', 'status');
  content.append(note);
}
function showLive() {
  const region = el('div');
  region.id = 'nanexus-live-monitor';
  content.append(region);
  const generation = liveGeneration;
  let attempts = 0;
  const attach = () => {
    if (current !== 'Live Monitor' || generation !== liveGeneration) return;
    if (window.__nanexusLive) {
      historicalCleanup = window.__nanexusLive.mount(region);
      title.focus();
      return;
    }
    if (++attempts >= 30) {
      unavailable('Unavailable — the accepted Live backend and UI have not been qualified on this Frigate profile.');
      return;
    }
    liveWait = setTimeout(attach, 100);
  };
  attach();
}
function showHistorical() {
  if (window.__nanexusHistorical) {
    historicalCleanup = window.__nanexusHistorical.mount(content);
    title.focus();
    return;
  }
  unavailable('Not connected — the Historical workspace remains loopback-only with separate operator admission. A Frigate session bridge is not implemented.');
}
function choose(name) {
  closeFlyout();
  if (current === name && !pane.hidden) return;
  releaseCurrent();
  current = name;
  pane.hidden = false;
  pane.classList.toggle('nx-live', name === 'Live Monitor');
  title.textContent = name;
  if (name === 'Live Monitor') showLive();
  else showHistorical();
}
let endListOpen = false;
function armEndList(event) {
  if (event.key !== 'Escape') return;
  try {
    endListOpen = Boolean(document.querySelector('select.nx-end:open'));
  } catch {
    endListOpen = false;
  }
}
function key(event) {
  if (event.key !== 'Escape' || event.defaultPrevented) return;
  if (endListOpen) {
    endListOpen = false;
    return;
  }
  if (!flyout.hidden) {
    closeFlyout();
    event.preventDefault();
  }
}
function pointerDismiss(event) {
  if (entry.contains(event.target) || flyout.contains(event.target)) return;
  const link = event.target.closest && event.target.closest('a[href]');
  if (link && !host.contains(link)) {
    leave(false);
    return;
  }
  if (!flyout.hidden) closeFlyout();
}
function nativeNav() {
  leave(false);
  positionEntry();
}
function hidden() {
  if (document.hidden) leave(false);
}
entry.addEventListener('click', toggleFlyout);
document.addEventListener('keydown', armEndList, true);
document.addEventListener('keydown', key);
document.addEventListener('click', pointerDismiss);
window.addEventListener('popstate', nativeNav);
window.addEventListener('hashchange', nativeNav);
document.addEventListener('visibilitychange', hidden);
window.__nanexusNavigation = {
  remove() {
    clearLiveWait();
    if (historicalCleanup) {
      historicalCleanup();
      historicalCleanup = null;
    }
    timers.forEach(clearTimeout);
    railObserver.disconnect();
    window.removeEventListener('load', positionEntry);
    window.removeEventListener('resize', positionEntry);
    document.removeEventListener('keydown', armEndList, true);
    document.removeEventListener('keydown', key);
    document.removeEventListener('click', pointerDismiss);
    window.removeEventListener('popstate', nativeNav);
    window.removeEventListener('hashchange', nativeNav);
    document.removeEventListener('visibilitychange', hidden);
    host.remove();
    delete window.__nanexusNavigation;
  },
};
})();
