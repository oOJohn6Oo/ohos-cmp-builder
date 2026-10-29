export const REPOSITORIES = [
  {id: 'cmp', label: 'CMP', path: 'CPF-KMP-CMP/compose-multiplatform'},
  {id: 'core', label: 'Core', path: 'CPF-KMP-CMP/compose-multiplatform-core'},
  {id: 'skiko', label: 'Skiko', path: 'CPF-KMP-CMP/skiko'},
];

const API = 'https://api.gitcode.com/api/v5/repos/';
const DAY = 86400000;
const PAGE_SIZE = 10;

function pause(ms, signal) {
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, ms);
    signal.addEventListener('abort', abort, {once: true});
    if (signal.aborted) abort();
  });
}

async function request(url, signal, fetcher) {
  for (let attempt = 0; ; attempt++) {
    signal.throwIfAborted();
    const controller = new AbortController();
    const abort = () => controller.abort(signal.reason);
    signal.addEventListener('abort', abort, {once: true});
    const timer = setTimeout(() => controller.abort(), 20000);
    let retryDelay = 1000 * (attempt + 1);
    try {
      const response = await fetcher(url, {signal: controller.signal, credentials: 'omit', cache: 'no-store'});
      if (!response.ok) {
        const error = new Error(`GitCode 请求失败（${response.status}）`);
        error.retryable = response.status === 429 || response.status >= 500;
        const retryAfter = response.headers.get('Retry-After');
        if (retryAfter) {
          const delay = /^\d+$/.test(retryAfter) ? Number(retryAfter) * 1000 : Date.parse(retryAfter) - Date.now();
          if (Number.isFinite(delay)) retryDelay = Math.max(1000, Math.min(delay, 30000));
        }
        throw error;
      }
      return await response.json();
    } catch (error) {
      signal.throwIfAborted();
      if (attempt >= 2 || error.retryable === false || error instanceof SyntaxError) throw error;
    } finally {
      clearTimeout(timer);
      signal.removeEventListener('abort', abort);
    }
    await pause(retryDelay, signal);
  }
}

export function descriptionExcerpt(body) {
  const text = String(body || '').replace(/<!--[\s\S]*?-->/g, '');
  const section = text.match(/^#{1,6}\s*(?:变更描述|变更内容|Summary|Description)\s*[:：]?\s*\n([\s\S]*?)(?=^#{1,6}\s|(?![\s\S]))/im);
  return (section ? section[1] : text)
    .replace(/```[\s\S]*?```/g, '')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^\s*[-*]\s*\[[ xX]\].*$/gm, '')
    .replace(/^\s*(?:#{1,6}|[-*>])\s*/gm, '')
    .replace(/[`*]/g, '').replace(/\s+/g, ' ').trim().slice(0, 240);
}

function avatarURL(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' ? url.href : '';
  } catch { return ''; }
}

// The list endpoint omits approvers; only PR details contain assignees[].accept.
export function parsePull(pull, repo, cutoff) {
  if (!pull || !Number.isInteger(pull.number) || typeof pull.state !== 'string') throw new Error('PR 数据格式异常');
  if (pull.state !== 'open') return null;
  const updated = Date.parse(pull.updated_at);
  if (!Number.isFinite(updated)) throw new Error('PR 更新时间缺失');
  if (updated < cutoff) return null;
  if (!Array.isArray(pull.assignees)) throw new Error('PR 审查人信息缺失');
  const reviewers = new Map();
  for (const person of pull.assignees) {
    if (!person || !person.id || !person.login || typeof person.accept !== 'boolean') throw new Error('审查状态无法识别');
    if (!person.accept) reviewers.set(String(person.id), {
      id: String(person.id), login: person.login, name: person.name || person.login,
      avatarUrl: avatarURL(person.avatar_url),
    });
  }
  return {
    reviewers: [...reviewers.values()], unassigned: pull.assignees.length === 0,
    pr: {
      id: `${repo.id}:${pull.number}`, repoId: repo.id, repoLabel: repo.label,
      number: pull.number, title: String(pull.title || `PR #${pull.number}`),
      description: descriptionExcerpt(pull.body),
      url: `https://gitcode.com/${repo.path}/pull/${pull.number}`,
      author: pull.user?.name || pull.user?.login || '', branch: pull.base?.ref || '',
      updatedAt: pull.updated_at, draft: pull.draft === true,
    },
  };
}

export function startScan({onUpdate, fetcher = fetch, now = Date.now()} = {}) {
  const controller = new AbortController();
  const {signal} = controller;
  const cutoff = now - 30 * DAY;
  const reviewers = new Map();
  const state = {
    loading: true, startedAt: now, finishedAt: null, reviewers: [], errors: [], unassigned: 0,
    repositories: REPOSITORIES.map(repo => ({...repo, phase: 'listing', total: 0, eligible: 0, checked: 0, failed: 0, excluded: 0, error: null})),
  };
  const emit = () => {
    if (!signal.aborted) onUpdate?.({...state,
      repositories: state.repositories.map(repo => ({...repo})), errors: [...state.errors],
      reviewers: [...reviewers.values()].map(person => ({...person, prs: [...person.prs.values()]})),
    });
  };
  const recordError = (repo, error, number) => {
    const message = error instanceof TypeError ? '网络连接失败，或 GitCode 暂时不允许访问' : error.message || '请求失败';
    state.errors.push({repoLabel: repo.label, ...(number ? {number} : {}), message});
    repo.error = message;
  };
  async function scanRepository(repo) {
    const listed = new Map();
    try {
      for (let page = 1; ; page++) {
        if (page > 100) throw new Error('PR 分页超出上限，结果不完整');
        const params = new URLSearchParams({state: 'open', sort: 'updated', direction: 'desc', per_page: String(PAGE_SIZE), page: String(page)});
        const pulls = await request(`${API}${repo.path}/pulls?${params}`, signal, fetcher);
        if (!Array.isArray(pulls)) throw new Error('PR 列表格式异常');
        let added = 0;
        for (const pull of pulls) {
          if (!Number.isInteger(pull?.number)) throw new Error('PR 序号缺失');
          if (!listed.has(pull.number)) added++;
          listed.set(pull.number, pull);
        }
        repo.total = listed.size;
        emit();
        // Updated-desc order puts all later pages outside the window once an old PR appears.
        if (pulls.length < PAGE_SIZE || pulls.some(pull => Date.parse(pull.updated_at) < cutoff)) break;
        if (!added) throw new Error('PR 分页重复，结果不完整');
      }
      const eligible = [...listed.values()].filter(pull => {
        const updated = Date.parse(pull.updated_at);
        return pull.state === 'open' && (!Number.isFinite(updated) || updated >= cutoff);
      });
      repo.eligible = eligible.length;
      repo.excluded = repo.total - eligible.length;
      repo.phase = 'scanning'; emit();
      for (const item of eligible) {
        signal.throwIfAborted();
        try {
          const pull = await request(`${API}${repo.path}/pulls/${item.number}`, signal, fetcher);
          if (pull?.number !== item.number) throw new Error('PR 详情与请求序号不一致');
          const result = parsePull(pull, repo, cutoff);
          if (result) {
            if (result.unassigned) state.unassigned++;
            for (const person of result.reviewers) {
              const previous = reviewers.get(person.id);
              if (!previous) reviewers.set(person.id, {...person, prs: new Map()});
              const current = reviewers.get(person.id);
              if (!current.avatarUrl && person.avatarUrl) current.avatarUrl = person.avatarUrl;
              current.prs.set(result.pr.id, result.pr);
            }
          }
        } catch (error) {
          signal.throwIfAborted();
          repo.failed++; recordError(repo, error, item.number);
        }
        repo.checked++; emit();
      }
      repo.phase = repo.failed ? 'error' : 'done';
    } catch (error) {
      if (signal.aborted) return;
      recordError(repo, error); repo.phase = 'error';
    }
    emit();
  }
  emit();
  // One asynchronous queue per repository: at most three requests in flight.
  const done = Promise.all(state.repositories.map(scanRepository)).then(() => {
    state.loading = false; state.finishedAt = Date.now(); emit();
  });
  return {cancel: () => controller.abort(), done};
}
