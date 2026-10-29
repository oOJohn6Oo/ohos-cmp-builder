const byId = (id) => document.getElementById(id);
const ui = Object.fromEntries(['chooser', 'chooser-title', 'profiles', 'home-empty', 'home-spinner', 'home-message', 'detail', 'detail-title', 'detail-avatar', 'detail-count', 'detail-number', 'detail-spinner', 'pr-list', 'detail-empty', 'detail-message', 'error-notice', 'error-message', 'scan-progress', 'announcer'].map((id) => [id, byId(id)]));
const profiles = new Map();
const cards = new Map();
let snapshot = emptySnapshot();
let scan = null;
let generation = 0;
let activeReviewer = null;
let lastReviewer = null;
let clientPromise;
let announceTimer;

function emptySnapshot() {
  return { loading: true, startedAt: Date.now(), finishedAt: null, repositories: [], reviewers: [], errors: [], unassigned: 0 };
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function httpUrl(value) {
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) ? url.href : '';
  } catch { return ''; }
}

function reviewerName(reviewer) {
  return String(reviewer.name || reviewer.login || reviewer.id);
}

function makeAvatar() {
  const frame = element('span', 'avatar-frame');
  const fallback = element('span', 'avatar-initials');
  const img = element('img');
  img.alt = '';
  img.loading = 'eager';
  img.decoding = 'async';
  img.referrerPolicy = 'no-referrer';
  img.hidden = true;
  frame.append(fallback, img);
  img.addEventListener('load', () => { img.hidden = false; fallback.hidden = true; });
  img.addEventListener('error', () => { img.hidden = true; fallback.hidden = false; });
  return { frame, fallback, img, url: '' };
}

function updateAvatar(avatar, reviewer) {
  avatar.fallback.textContent = Array.from(reviewerName(reviewer).trim())[0]?.toUpperCase() || '?';
  const url = httpUrl(reviewer.avatarUrl);
  if (url === avatar.url) return;
  avatar.url = url;
  avatar.img.hidden = true;
  avatar.fallback.hidden = false;
  if (url) avatar.img.src = url;
  else avatar.img.removeAttribute('src');
}

function incomplete() {
  return snapshot.errors.length > 0 || snapshot.repositories.some((repo) => repo.phase === 'error' || repo.failed > 0);
}

function countText(reviewer) {
  return `${incomplete() ? '至少 ' : ''}${reviewer.prs.length} 项待审查`;
}

function makeProfile(reviewer) {
  const item = element('li', 'profile');
  const link = element('a', 'profile-link');
  link.href = `#reviewer=${encodeURIComponent(String(reviewer.id))}`;
  const avatar = makeAvatar();
  const name = element('span', 'profile-name');
  const count = element('span', 'profile-count');
  const number = element('span');
  const spinner = element('span', 'spinner');
  spinner.setAttribute('aria-hidden', 'true');
  count.append(number, spinner);
  link.append(avatar.frame, name, count);
  item.append(link);
  ui.profiles.append(item);
  return { item, link, avatar, name, number, spinner };
}

function renderProfiles() {
  const present = new Set();
  for (const reviewer of snapshot.reviewers) {
    const id = String(reviewer.id);
    present.add(id);
    if (!profiles.has(id)) profiles.set(id, makeProfile(reviewer));
    const profile = profiles.get(id);
    const name = reviewerName(reviewer);
    profile.name.textContent = name;
    profile.name.title = name;
    profile.number.textContent = countText(reviewer);
    profile.spinner.hidden = !snapshot.loading;
    profile.link.setAttribute('aria-label', `${name}，${countText(reviewer)}${snapshot.loading ? '，仍在扫描' : ''}`);
    updateAvatar(profile.avatar, reviewer);
  }
  for (const [id, profile] of profiles) {
    if (!present.has(id)) { profile.item.remove(); profiles.delete(id); }
  }
  ui['home-empty'].hidden = snapshot.reviewers.length > 0;
  ui['home-spinner'].hidden = !snapshot.loading;
  ui['home-message'].textContent = snapshot.loading ? 'Loading…' : incomplete() ? '暂未发现待审查请求，结果不完整。' : '当前没有待审查请求';
}

function makeCard() {
  const item = element('li', 'pr-card');
  const topline = element('div', 'pr-topline');
  const repo = element('span', 'repo-badge');
  const number = element('span');
  const draft = element('span', 'pr-draft', '草稿');
  topline.append(repo, number, draft);
  const title = element('h2', 'pr-title');
  const description = element('p', 'pr-description');
  const footer = element('div', 'pr-footer');
  const meta = element('div', 'pr-meta');
  const author = element('span');
  const branch = element('span');
  const updated = element('span');
  meta.append(author, branch, updated);
  const open = element('a', 'pr-open', '打开 GitCode ↗');
  open.target = '_blank';
  open.rel = 'noopener noreferrer';
  footer.append(meta, open);
  item.append(topline, title, description, footer);
  ui['pr-list'].append(item);
  return { item, repo, number, draft, title, description, author, branch, updated, open };
}

function authorName(author) {
  return typeof author === 'object' && author ? String(author.name || author.login || '未知作者') : String(author || '未知作者');
}

function dateText(value) {
  const date = new Date(value);
  if (!value || Number.isNaN(date.getTime())) return '更新时间未知';
  return `${new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(date)} 更新`;
}

function updateCard(card, pr) {
  card.repo.textContent = pr.repoLabel || pr.repoId;
  card.number.textContent = `#${pr.number}`;
  card.draft.hidden = !pr.draft;
  card.title.textContent = pr.title || '未命名请求';
  card.description.textContent = pr.description || '';
  card.description.hidden = !pr.description;
  card.author.textContent = `作者 · ${authorName(pr.author)}`;
  card.branch.textContent = pr.branch ? `分支 · ${pr.branch}` : '';
  card.branch.hidden = !pr.branch;
  card.updated.textContent = dateText(pr.updatedAt);
  const url = httpUrl(pr.url);
  if (url) {
    card.open.href = url;
    card.open.removeAttribute('aria-disabled');
    card.open.removeAttribute('tabindex');
  } else {
    card.open.removeAttribute('href');
    card.open.setAttribute('aria-disabled', 'true');
    card.open.tabIndex = -1;
  }
  card.open.setAttribute('aria-label', `在 GitCode 打开 ${pr.repoLabel || pr.repoId} #${pr.number}：${pr.title || '未命名请求'}（新标签页）`);
}

const detailAvatar = makeAvatar();
ui['detail-avatar'].append(detailAvatar.frame);

function renderDetail() {
  if (activeReviewer === null) return;
  const reviewer = snapshot.reviewers.find((entry) => String(entry.id) === activeReviewer);
  ui['detail-title'].textContent = reviewer ? reviewerName(reviewer) : '待审查请求';
  ui['detail-avatar'].hidden = !reviewer;
  ui['detail-number'].textContent = reviewer ? countText(reviewer) : snapshot.loading ? '正在查找待办…' : incomplete() ? '结果不完整' : '暂无待办';
  ui['detail-spinner'].hidden = !snapshot.loading;
  if (reviewer) updateAvatar(detailAvatar, reviewer);
  const present = new Set();
  for (const pr of reviewer?.prs || []) {
    const key = `${pr.repoId}:${pr.id ?? pr.number}`;
    present.add(key);
    if (!cards.has(key)) cards.set(key, makeCard());
    updateCard(cards.get(key), pr);
  }
  for (const [key, card] of cards) {
    if (!present.has(key)) { card.item.remove(); cards.delete(key); }
  }
  ui['detail-empty'].hidden = present.size > 0 || snapshot.loading;
  ui['detail-message'].textContent = incomplete() ? '暂未发现这位审查人的待办，结果不完整。' : '近 30 天没有发现这位审查人的待审查请求。';
}

function renderStatus() {
  byId('refresh').hidden = snapshot.loading;
  byId('retry').hidden = snapshot.loading;
  const partial = incomplete();
  ui['error-notice'].hidden = !partial;
  const failedRepos = [...new Set([...snapshot.errors.map((error) => error.repoLabel), ...snapshot.repositories.filter((repo) => repo.phase === 'error' || repo.failed > 0).map((repo) => repo.label)].filter(Boolean))];
  ui['error-message'].textContent = `${failedRepos.length ? failedRepos.join('、') + ' 的部分请求未能获取。' : '部分请求未能获取。'}${snapshot.loading ? '扫描仍在进行。' : '待办数量仅包含已发现的结果。'}`;
  const checked = snapshot.repositories.reduce((total, repo) => total + (repo.checked || 0), 0);
  const eligible = snapshot.repositories.reduce((total, repo) => total + (repo.eligible || 0), 0);
  const listing = snapshot.repositories.some((repo) => repo.phase === 'listing');
  if (snapshot.loading) {
    ui['scan-progress'].textContent = checked ? `正在扫描 · 已核对 ${checked}${!listing && eligible ? ` / ${Math.max(eligible, checked)}` : ''} 个请求` : '正在扫描 CMP · Core · Skiko';
  } else {
    const time = snapshot.finishedAt ? new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(snapshot.finishedAt)) : '';
    ui['scan-progress'].textContent = `${partial ? '扫描结束 · 结果不完整' : '已完成扫描'}${time ? ` · ${time}` : ''}${snapshot.unassigned ? ` · ${snapshot.unassigned} 个请求未指定审查人` : ''}`;
  }
  clearTimeout(announceTimer);
  announceTimer = setTimeout(() => {
    ui.announcer.textContent = snapshot.loading ? `正在扫描，已发现 ${snapshot.reviewers.length} 位审查人。${partial ? '部分结果获取失败。' : ''}` : `${partial ? '扫描结束，结果不完整。' : '扫描完成。'}已发现 ${snapshot.reviewers.length} 位审查人。`;
  }, snapshot.loading ? 900 : 0);
}

function render() {
  renderProfiles();
  renderDetail();
  renderStatus();
}

function route(moveFocus = false) {
  const requested = new URLSearchParams(location.hash.slice(1)).get('reviewer');
  const previous = activeReviewer;
  activeReviewer = requested || null;
  if (previous !== activeReviewer) {
    if (previous !== null) lastReviewer = previous;
    for (const card of cards.values()) card.item.remove();
    cards.clear();
  }
  ui.chooser.hidden = activeReviewer !== null;
  ui.detail.hidden = activeReviewer === null;
  renderDetail();
  if (moveFocus) {
    if (activeReviewer !== null) ui['detail-title'].focus();
    else (profiles.get(lastReviewer)?.link || ui['chooser-title']).focus();
    window.scrollTo({ top: 0, behavior: 'instant' });
  }
  document.title = activeReviewer === null ? 'CMP / REVIEW' : '待审查请求 · CMP / REVIEW';
}

async function refresh() {
  const token = ++generation;
  scan?.cancel();
  scan = null;
  snapshot = emptySnapshot();
  render();
  try {
    clientPromise ||= import('./review-client.js');
    const client = await clientPromise;
    if (token !== generation) return;
    scan = client.startScan({ onUpdate(next) {
      if (token !== generation) return;
      snapshot = next;
      render();
    } });
    await scan.done;
    if (token !== generation) return;
    if (snapshot.loading) {
      snapshot = { ...snapshot, loading: false, finishedAt: Date.now() };
      render();
    }
  } catch (error) {
    if (token !== generation) return;
    clientPromise = null;
    snapshot = { ...snapshot, loading: false, finishedAt: Date.now(), errors: [...snapshot.errors, { repoLabel: '', message: String(error?.message || error) }] };
    render();
  }
}

byId('refresh').addEventListener('click', refresh);
byId('retry').addEventListener('click', refresh);
byId('back').addEventListener('click', () => { location.hash = ''; });
window.addEventListener('hashchange', () => route(true));
window.addEventListener('pagehide', () => { generation++; scan?.cancel(); });
window.addEventListener('pageshow', (event) => { if (event.persisted) refresh(); });
route();
refresh();
