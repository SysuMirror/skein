// transcript 同步：把 cc 的会话正文按轮上传到控制面，失败落本地队列。
// 只做传输，不解析内容；控制面按 (session_id, turn_index) 幂等。
import { existsSync, mkdirSync, readFileSync, readdirSync, appendFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

export function findTranscript(homeDir, sessionId) {
  const root = join(homeDir, '.claude', 'projects');
  if (!existsSync(root)) return null;
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const candidate = join(root, entry.name, `${sessionId}.jsonl`);
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

export class TranscriptSync {
  constructor({ url, token, queuePath, fetchImpl = globalThis.fetch, homeDir = process.env.HOME }) {
    this.url = url;
    this.token = token;
    this.queuePath = queuePath;
    this.fetch = fetchImpl;
    this.homeDir = homeDir;
  }

  async syncTurn({ sessionId, turnIndex, headSha = null, branch = null }) {
    const path = findTranscript(this.homeDir, sessionId);
    if (!path) return { ok: false, reason: 'no_transcript' };
    const entry = {
      session_id: sessionId,
      turn_index: turnIndex,
      data: readFileSync(path).toString('base64'),
      head_sha: headSha,
      branch,
    };
    const ok = await this._post(entry);
    if (!ok) this._enqueue(entry);
    return { ok, queued: !ok };
  }

  async flush() {
    if (!existsSync(this.queuePath)) return { sent: 0, left: 0 };
    const lines = readFileSync(this.queuePath, 'utf8').split('\n').filter(Boolean);
    const left = [];
    let sent = 0;
    for (const line of lines) {
      let entry;
      try { entry = JSON.parse(line); } catch { continue; }
      if (await this._post(entry)) sent += 1;
      else left.push(line);
    }
    writeFileSync(this.queuePath, left.length ? left.join('\n') + '\n' : '');
    return { sent, left: left.length };
  }

  async _post(entry) {
    try {
      const res = await this.fetch(`${this.url}/internal/worker/turn`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Skein-Token': this.token },
        body: JSON.stringify(entry),
      });
      // 409 = 这一轮已经被别的节点写过，视为已处理
      return res.ok || res.status === 409;
    } catch {
      return false;
    }
  }

  _enqueue(entry) {
    mkdirSync(dirname(this.queuePath), { recursive: true });
    appendFileSync(this.queuePath, JSON.stringify(entry) + '\n');
  }
}
