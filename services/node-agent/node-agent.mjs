#!/usr/bin/env node
// 雁阵 node agent（机器/pod 级常驻）——集群里的一个节点。
// 职责：拨入 controller 注册+心跳（在线自动检测）；按指令在本机创建/销毁 worker；
//       worker = 一个 Claude Code 会话：create 时初始化会话拿 session_id，
//       派单用 --resume 续同一会话（上下文跨单累积），流式回报进度。
// 不自研 agent：改代码的活全部交给 cc。
//
// 运行：CONTROLLER_URL=wss://skein.example.com/api/agent/channel \
//       AGENT_TOKEN=... NODE_ID=node-1 PLATFORMS=my-service \
//       REPO_MAP='{"my-service":"/path/to/repo"}' node node-agent.mjs
import WebSocket, { WebSocketServer } from 'ws';
import { spawn, execFileSync } from 'node:child_process';
import { createServer } from 'node:http';
import { hostname, networkInterfaces } from 'node:os';
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { TranscriptSync } from './transcript-sync.mjs';
import { buildSandboxArgs, sandboxSupported, unitName } from './sandbox.mjs';
import { workerEnvironment } from './worker-env.mjs';

const env = process.env;
// 控制面地址：CONTROLLER_URL 优先；只配了 SKEIN_CONTROL_URL 时从它推出来（http→ws）。
const SKEIN_CONTROL_URL = (env.SKEIN_CONTROL_URL || '').replace(/\/+$/, '');
const SKEIN_TOKEN = env.SKEIN_TOKEN || '';
const CONTROLLER_URL =
  env.CONTROLLER_URL ||
  (SKEIN_CONTROL_URL ? `${SKEIN_CONTROL_URL.replace(/^http/, 'ws')}/api/agent/channel` : '');
if (!CONTROLLER_URL) {
  console.error(
    '[node] set CONTROLLER_URL (or SKEIN_CONTROL_URL) to the control plane,' +
      ' e.g. ws://127.0.0.1:7800/api/agent/channel'
  );
  process.exit(1);
}
// 注册令牌：AGENT_TOKEN 优先，没配就等于 SKEIN_TOKEN（同一个控制面主令牌）
const AGENT_TOKEN = env.AGENT_TOKEN || SKEIN_TOKEN;
const NODE_ID = env.NODE_ID || `n-${hostname()}`;
const PLATFORMS = (env.PLATFORMS || '').split(',').filter(Boolean);
const REPO_MAP = JSON.parse(env.REPO_MAP || '{}');
const MAX_WORKERS = Number(env.MAX_WORKERS || 3);
const CLAUDE_BIN = env.CLAUDE_BIN || 'claude';
const HEARTBEAT_MS = 15_000;
const PUSH_BRANCH = env.PUSH_BRANCH === '1';
// 沙箱：默认关闭；开启前需按 docs/sandbox.md 完成宿主准备
const SANDBOX_ENABLED = env.SANDBOX_ENABLED === '1' && sandboxSupported();
if (env.SANDBOX_ENABLED === '1' && !SANDBOX_ENABLED) console.log('[node] SANDBOX_ENABLED=1 但 systemd-run 不可用，退回直接运行');
// worker 直连端口：room 名册里广播本 node 的 peer_url，成员 node 互拨（controller 仅兜底）
const PEER_PORT = Number(env.PEER_PORT || 9101);
function lanIp() {
  for (const list of Object.values(networkInterfaces())) for (const i of list || []) if (i.family === 'IPv4' && !i.internal) return i.address;
  return '127.0.0.1';
}
const PEER_URL = env.PEER_URL || `ws://${lanIp()}:${PEER_PORT}/peer`;
const roomPeers = new Map();   // room_id → Set<ws>

const stateDir = join(dirname(fileURLToPath(import.meta.url)), '..', 'data');
mkdirSync(stateDir, { recursive: true });
const stateFile = join(stateDir, 'node-state.json');
let state = { workers: {} };
try { state = JSON.parse(readFileSync(stateFile, 'utf8')); } catch {}
const saveState = () => writeFileSync(stateFile, JSON.stringify(state, null, 2));

// transcript 同步：控制面地址未配置时整条链路不启用
const transcriptSync = SKEIN_CONTROL_URL
  ? new TranscriptSync({ url: SKEIN_CONTROL_URL, token: SKEIN_TOKEN, queuePath: join(stateDir, 'transcript-queue.jsonl') })
  : null;
if (transcriptSync) {
  setInterval(() => transcriptSync.flush().catch((e) => console.log('[node] transcript 补传失败', e.message)), 30_000);
}

const busy = new Map();      // worker_id → child process
const procs = new Map();     // worker_id → 正在跑的 cc 子进程（cancel 要能杀到它）
const cancelled = new Set();
let ws = null, backoff = 1000, hbTimer = null;

function send(msg) { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg)); }
function killGroup(child, signal) {
  // 子进程用独立进程组（spawn detached）起，杀整组才能连 cc 派生的子命令一起收掉
  try { process.kill(-child.pid, signal); } catch { try { child.kill(signal); } catch { /* 已退出 */ } }
}
function git(repo, ...args) { return execFileSync('git', ['-C', repo, ...args], { encoding: 'utf8', timeout: 60_000 }).trim(); }
const sandboxed = env.IS_SANDBOX === '1' || existsSync('/.dockerenv') || !!env.POD_NAME;
const ccEnv = { ...env, ...(sandboxed ? { IS_SANDBOX: '1' } : {}) };

const WORKER_RULES = `你是「雁阵」系统的 Worker，在隔离环境中自主完成需求单。工作规则：
1. 只在当前仓库目录内工作；改动提交到指定修复分支，绝不改动/合入基线分支（master/main）。
2. 流程：理解需求 → 定位代码 → 最小修复/实现 → 自测（能跑测试就跑，跑不了说明原因）→ git commit（信息含需求单编号）。
3. 不要 push、不要部署、不要改线上配置——交付后由人工验收。
4. 遇到无法解决的阻塞（缺凭据/环境损坏），如实总结阻塞原因结束，不要伪造结果。
5. 最终输出一段中文总结：改了什么、怎么自测的、遗留风险。
6. 你与派单方共用一个长期会话：记住此前单子的上下文，但每单只改当前分支。
7. 工具受白名单限制（只读/编辑/git/测试类）：命令被拒时不要尝试绕过，换允许的方式或如实报告阻塞。
8. 你在沙箱副本仓库工作：改动只留在本副本的修复分支，合入真实仓库由人工验收后执行。`;

/** 跑一次 cc（init 或 --resume），流式解析；返回 {ok, summary, sessionId} */
// 沙箱工具白名单：只读/编辑/ git /测试类命令；rm、系统命令、网络写操作一律拒（-p 模式自动 deny）
const SAFE_TOOLS = env.WORKER_ALLOWED_TOOLS || [
  'Read', 'Glob', 'Grep', 'Edit', 'Write',
  'Bash(git:*)', 'Bash(ls:*)', 'Bash(cat:*)', 'Bash(head:*)', 'Bash(tail:*)', 'Bash(grep:*)',
  'Bash(find:*)', 'Bash(wc:*)', 'Bash(diff:*)', 'Bash(node --check:*)', 'Bash(npm test:*)',
  'Bash(npm run test:*)', 'Bash(npx vitest:*)', 'Bash(python3 -m pytest:*)', 'Bash(pwd)', 'Bash(echo:*)',
].join(',');
// 每单独立 HOME：cc 的配置与缓存不落进仓库，也不写节点的 ~。
function jobHome(workerId, repo) {
  const home = join(dirname(repo), 'home-' + (workerId || 'adhoc'));
  mkdirSync(join(home, '.claude'), { recursive: true });
  mkdirSync(join(home, '.npm'), { recursive: true });
  const seedRoot = env.HOME || '';
  const seeds = [
    [join(seedRoot, '.claude', 'settings.json'), join(home, '.claude', 'settings.json')],
    [join(seedRoot, '.claude.json'), join(home, '.claude.json')],
  ];
  for (const [src, dst] of seeds) {
    if (existsSync(src) && !existsSync(dst)) {
      try { copyFileSync(src, dst); } catch { /* 读不到就算了 */ }
    }
  }
  return home;
}
function runClaude(repo, prompt, { resume = null, onProgress = () => {}, timeoutMs = 20 * 60_000, workerId = '' } = {}) {
  return new Promise((resolve) => {
    const home = jobHome(workerId, repo);
    // 容器内显式 IS_SANDBOX 才全 bypass；裸机默认白名单+acceptEdits（改不崩、跑不崩）
    const fullBypass = sandboxed && env.WORKER_FULL_BYPASS === '1';
    const args = ['-p', prompt, '--output-format', 'stream-json', '--verbose', '--append-system-prompt', WORKER_RULES];
    if (fullBypass) args.push('--dangerously-skip-permissions');
    else args.push('--permission-mode', 'acceptEdits', '--allowedTools', SAFE_TOOLS);
    if (resume) args.push('--resume', resume);
    let file = CLAUDE_BIN, execArgs = args;
    if (SANDBOX_ENABLED) {
      execArgs = buildSandboxArgs({
        unit: unitName(`agent-${NODE_ID}`, workerId || Date.now().toString(36)),
        workdir: repo,
        home,
        command: CLAUDE_BIN,
        args,
      });
      file = 'systemd-run';
    }
    const child = spawn(file, execArgs, {
      cwd: repo,
      // 控制面令牌只属于 node-agent，不进 worker。
      env: { ...workerEnvironment(ccEnv), HOME: home },
      stdio: ['ignore', 'pipe', 'pipe'],
      // 独立进程组：cancel/超时时按组杀，cc 拉起的子命令一并收掉
      detached: true,
    });
    if (workerId) procs.set(workerId, child);
    let buf = '', lastEmit = 0, finalText = '', sessionId = resume, errored = null, done = false;
    const timer = setTimeout(() => { killGroup(child, 'SIGTERM'); errored = 'cc 超时被终止'; }, timeoutMs);
    const finish = (ok) => { if (done) return; done = true; clearTimeout(timer); if (workerId) procs.delete(workerId); resolve({ ok, summary: (finalText || errored || '').slice(0, 4000), sessionId }); };
    const handleLine = (line) => {
      if (!line.trim()) return;
      let m; try { m = JSON.parse(line); } catch { return; }
      if (m.session_id) sessionId = m.session_id;
      if (m.type === 'assistant' && m.message?.content) {
        for (const c of m.message.content) {
          if (c.type === 'text' && c.text) finalText = c.text;
          if (c.type === 'tool_use' && Date.now() - lastEmit > 2500) {
            lastEmit = Date.now();
            const brief = c.name === 'Bash' ? String(c.input?.command || '').slice(0, 80)
              : ['Edit', 'Write', 'Read'].includes(c.name) ? String(c.input?.file_path || '').split('/').pop()
              : JSON.stringify(c.input || {}).slice(0, 60);
            onProgress(`工具 ${c.name}`, brief);
          }
        }
      } else if (m.type === 'result') {
        errored = m.subtype !== 'success' ? (m.result || m.subtype) : null;
        if (m.result) finalText = m.result;
      }
    };
    child.stdout.on('data', (d) => { buf += d.toString(); let i; while ((i = buf.indexOf('\n')) >= 0) { handleLine(buf.slice(0, i)); buf = buf.slice(i + 1); } });
    child.stderr.on('data', (d) => { const s = d.toString(); if (s.trim()) onProgress('cc stderr', s.slice(0, 120)); });
    child.on('error', (e) => { errored = `cc 启动失败：${e.message}`; finish(false); });
    child.on('close', (code) => { handleLine(buf); finish(code === 0 && !errored); });
  });
}

// 每个 worker 独立 git worktree：多会话同节点互不踩分支/工作区
function ensureWorktree(workerId, repo) {
  const wt = join(dirname(repo), 'wt-' + workerId);
  if (!existsSync(wt)) {
    execFileSync('git', ['-C', repo, 'worktree', 'add', '--detach', wt], { encoding: 'utf8', timeout: 120_000 });
  }
  return wt;
}
function removeWorktree(workerId, repo) {
  const wt = join(dirname(repo), 'wt-' + workerId);
  try { execFileSync('git', ['-C', repo, 'worktree', 'remove', '--force', wt], { timeout: 60_000 }); } catch {}
}
function removeHome(workerId, repo) {
  const home = join(dirname(repo), 'home-' + (workerId || 'adhoc'));
  try {
    // Go 模块缓存是只读的：先加写权限再删，否则 rm -rf 静默失败留垃圾
    try { execFileSync('chmod', ['-R', 'u+w', home], { timeout: 120_000 }); } catch {}
    execFileSync('rm', ['-rf', home], { timeout: 120_000 });
  } catch {}
}
// 单子结束（成功/失败/取消）后清掉工作目录与沙箱 HOME；分支本身在共享仓库里，不动（PR 还要用）
function cleanupWorker(workerId, w) {
  const target = w || state.workers[workerId];
  if (!target?.repo) return;
  removeWorktree(workerId, target.repo);
  removeHome(workerId, target.repo);
  if (state.workers[workerId] === target) {
    delete state.workers[workerId];
    saveState();
  }
  console.log(`[node] worker ${workerId} 沙箱已清理（worktree + home）`);
}
// 启动时为旧会话补 worktree（升级迁移）
for (const [wid, w] of Object.entries(state.workers)) {
  if (!w.wt && w.repo && existsSync(w.repo)) { try { w.wt = ensureWorktree(wid, w.repo); } catch (e) { console.log('[node] worktree 迁移失败', wid, e.message); } }
}
// 历史残留：worktree 与 home 都已不在的状态条目直接丢弃（会话已死，resume 无从谈起）
for (const [wid, w] of Object.entries(state.workers)) {
  const wtGone = !w.wt || !existsSync(w.wt);
  const homeGone = !w.repo || !existsSync(join(dirname(w.repo), 'home-' + wid));
  if (wtGone && homeGone) {
    delete state.workers[wid];
    console.log(`[node] 清理失效会话 ${wid}（工作目录与沙箱均已不存在）`);
  }
}
saveState();

async function createWorker(msg) {
  const { worker_id, name, platform } = msg;
  // 节点自己知道仓库在哪：控制面不配 SKEIN_PLATFORM_REPOS 也能派。
  const repo = msg.repo || REPO_MAP[platform] || '';
  // 容量限制的是并发执行，不是历史会话数：空闲会话只占状态，不占进程。
  if (busy.size >= MAX_WORKERS) { send({ t: 'worker.create_failed', worker_id, reason: '节点并发已满' }); return; }
  if (!repo || !existsSync(repo)) { send({ t: 'worker.create_failed', worker_id, reason: `仓库不存在 ${repo || '(未配置)'}` }); return; }
  let wt;
  try { wt = ensureWorktree(worker_id, repo); }
  catch (e) { send({ t: 'worker.create_failed', worker_id, reason: 'worktree 创建失败：' + e.message }); return; }
  const initPrompt = `你是雁阵 Worker「${name || worker_id}」，使命：${msg.purpose || '通用平台 Worker'}。负责平台 ${platform || '通用'}，仓库 ${repo}。这是初始化会话：用 ls 与 git log --oneline -5 快速熟悉仓库结构，然后只回复一句"就绪"。不要修改任何文件。`;
  const r = await runClaude(wt, initPrompt, { timeoutMs: 5 * 60_000, workerId: worker_id });
  if (!r.sessionId) { send({ t: 'worker.create_failed', worker_id, reason: r.summary || 'cc 未返回 session_id' }); return; }
  state.workers[worker_id] = { session_id: r.sessionId, platform, repo, wt, name: name || worker_id };
  saveState();
  send({ t: 'worker.created', worker_id, session_id: r.sessionId, platform, repo });
  console.log(`[node] worker ${worker_id} 会话建立 ${r.sessionId}`);
}

async function runTask(msg) {
  const { ticket, context = {}, branch_policy, worker_id } = msg;
  const w = state.workers[worker_id];
  if (!w) { send({ t: 'nack', ticket_id: ticket.id, worker_id, reason: '会话不存在' }); return; }
  if (busy.has(worker_id)) { send({ t: 'nack', ticket_id: ticket.id, worker_id, reason: 'busy（会话单并发）' }); return; }
  const repo = w.wt || w.repo || context.local || REPO_MAP[ticket.platform];
  let branch = branch_policy || `fix/${ticket.code.toLowerCase()}`;
  const baseCommit = git(repo, 'rev-parse', '--short', 'HEAD');
  send({ t: 'ack', ticket_id: ticket.id, worker_id });
  send({ t: 'progress', ticket_id: ticket.id, worker_id, step: '开始', message: `会话 ${w.session_id.slice(0, 8)} 续接，基线 ${baseCommit}，分支 ${branch}`, pct: 5 });
  const prompt = `需求单 ${ticket.code}（${ticket.kind === 'bug' ? '缺陷' : '功能需求'} / 平台 ${ticket.platform} / 级别 ${ticket.severity}）
标题：${ticket.title}
摘要：${ticket.summary || ''}
来源：${ticket.source_url || '内部'}
工作分支：${branch}（先 git checkout -b ${branch}，全部改动提交到此分支）
请完成这张需求单。`;
  const p = runClaude(repo, prompt, {
    resume: w.session_id,
    workerId: worker_id,
    onProgress: (step, message) => send({ t: 'progress', ticket_id: ticket.id, worker_id, step, message }),
  });
  busy.set(worker_id, p);
  const cc = await p;
  busy.delete(worker_id);
  // 配额/限流只在单子确实失败时报：审计类任务的总结里常出现 "429/限流" 字样，不能当失败信号
  if (!cc.ok && /quota|429|rate.?limit|insufficient|余额|配额/i.test(String(cc.summary || ''))) {
    console.log(`[node] worker ${worker_id} 配额/限流失败：${String(cc.summary).slice(0, 160)}`);
  }
  if (cancelled.has(ticket.id)) { cancelled.delete(ticket.id); return; }
  let branchNow = '', commits = '', diffstat = '', remote = '';
  try {
    branchNow = git(repo, 'branch', '--show-current');
    commits = git(repo, 'log', '--oneline', `${baseCommit}..HEAD`);
    diffstat = git(repo, 'diff', '--stat', `${baseCommit}..HEAD`);
    remote = git(repo, 'remote', 'get-url', 'origin');
    if (PUSH_BRANCH && commits) git(repo, 'push', '-u', 'origin', branchNow);
  } catch {}
  if (cc.sessionId) { w.session_id = cc.sessionId; saveState(); }
  send({
    t: 'result', ticket_id: ticket.id, worker_id,
    result: { ok: cc.ok, branch: branchNow, base_commit: baseCommit, commits, diffstat, pushed: PUSH_BRANCH && !!commits, summary: cc.summary, session_id: cc.sessionId, tests: '见总结（cc 自测报告）', platform: ticket.platform || w.platform || '', remote },
  });
  const finishCleanup = () => cleanupWorker(worker_id, w);
  if (transcriptSync) {
    const sessionId = cc.sessionId || w.session_id;
    const turn = (w.turn || 0) + 1;
    w.turn = turn;
    saveState();
    let headSha = '';
    try { headSha = git(repo, 'rev-parse', 'HEAD'); } catch {}
    transcriptSync
      .syncTurn({ sessionId, turnIndex: turn, headSha, branch: branchNow })
      .then((r) => { if (!r.ok && r.reason !== 'no_transcript') console.log('[node] transcript 已入队', sessionId.slice(0, 8), turn); })
      .catch((e) => console.log('[node] transcript 同步异常', e.message))
      .finally(finishCleanup);
  } else {
    finishCleanup();
  }
}

/* ---- worker 直连：peer 服务 + 名册互拨 + room 发言 ---- */
function postRoom(roomId, workerId, kind, body) {
  const peers = roomPeers.get(roomId);
  const msg = { t: 'room.msg', room_id: roomId, from: workerId, kind, body };
  let direct = 0;
  if (peers) for (const p of peers) if (p.readyState === WebSocket.OPEN) { p.send(JSON.stringify(msg)); direct++; }
  // 落账抄送；直连全失败才请 controller 兜底转发
  send({ t: 'room.post', room_id: roomId, worker_id: workerId, kind, body, via: direct ? 'direct' : 'relay' });
  return direct;
}
function dialRoster(roomId, members) {
  const set = roomPeers.get(roomId) || new Set();
  roomPeers.set(roomId, set);
  for (const m of members) {
    if (!m.peer_url || m.peer_url === PEER_URL) continue;
    if ([...set].some(p => p._peerUrl === m.peer_url && p.readyState <= WebSocket.OPEN)) continue;
    const p = new WebSocket(m.peer_url);
    p._peerUrl = m.peer_url;
    p.on('open', () => p.send(JSON.stringify({ t: 'peer.hello', room_id: roomId, worker_id: m.worker_id, node_id: NODE_ID, token: AGENT_TOKEN })));
    p.on('message', (raw) => {
      let x; try { x = JSON.parse(raw.toString()); } catch { return; }
      if (x.t === 'room.msg') console.log(`[node] 雁鸣直连 ${x.room_id} ← ${x.from}: ${String(x.body).slice(0, 160)}`);
    });
    p.on('close', () => set.delete(p));
    p.on('error', () => set.delete(p));
    set.add(p);
  }
}
{
  const httpSrv = createServer((req, res) => {
    // 本地回环口：供 cc 的 MCP room 桥/自检调用发言
    if (req.method === 'POST' && req.url === '/room/post' && (req.socket.remoteAddress === '127.0.0.1' || req.socket.remoteAddress === '::1')) {
      let b = '';
      req.on('data', d => b += d);
      req.on('end', () => {
        try {
          const m = JSON.parse(b);
          const n = postRoom(m.room_id, m.worker_id, m.kind || 'note', m.body);
          res.end(JSON.stringify({ ok: true, direct_peers: n }));
        } catch (e) { res.statusCode = 400; res.end(JSON.stringify({ error: e.message })); }
      });
      return;
    }
    res.statusCode = 404; res.end();
  });
  const wss = new WebSocketServer({ server: httpSrv, path: '/peer' });
  wss.on('connection', (p) => {
    p.on('message', (raw) => {
      let m; try { m = JSON.parse(raw.toString()); } catch { return; }
      if (m.t === 'peer.hello') {
        if (m.token !== AGENT_TOKEN) { p.close(); return; }
        // 入连接只收不发（发送走本端拨出的连接，避免双份）
        console.log(`[node] 雁鸣直连建立 room=${m.room_id} ← ${m.node_id}`);
      } else if (m.t === 'room.msg') {
        console.log(`[node] 雁鸣直连 ${m.room_id} ← ${m.from}: ${String(m.body).slice(0, 160)}`);
      }
    });
    p.on('close', () => { for (const set of roomPeers.values()) set.delete(p); });
    p.on('error', () => {});
  });
  httpSrv.listen(PEER_PORT, '0.0.0.0', () => console.log(`[node] peer 直连口 :${PEER_PORT}（${PEER_URL}）`));
}

function connect() {
  ws = new WebSocket(CONTROLLER_URL);
  ws.on('open', () => {
    backoff = 1000;
    send({
      t: 'node.register', node_id: NODE_ID, token: AGENT_TOKEN,
      name: env.NODE_NAME || NODE_ID, host: hostname(), pod: env.POD_NAME || '',
      platforms: PLATFORMS, repos: REPO_MAP, max_workers: MAX_WORKERS, peer_url: PEER_URL,
      meta: { version: '0.3.0', push: PUSH_BRANCH },
      workers: Object.entries(state.workers).map(([id, w]) => ({ worker_id: id, session_id: w.session_id, platform: w.platform, repo: w.repo, name: w.name, busy: busy.has(id) })),
    });
    clearInterval(hbTimer);
    hbTimer = setInterval(() => send({ t: 'node.heartbeat' }), HEARTBEAT_MS);
  });
  ws.on('message', (raw) => {
    let m; try { m = JSON.parse(raw.toString()); } catch { return; }
    if (m.t === 'node.registered') console.log(`[node] ${NODE_ID} 注册成功，本地活会话 ${Object.keys(state.workers).length}`);
    else if (m.t === 'worker.create') createWorker(m).catch(e => send({ t: 'worker.create_failed', worker_id: m.worker_id, reason: e.message }));
    else if (m.t === 'worker.destroy') {
      const w = state.workers[m.worker_id];
      if (w?.repo) removeWorktree(m.worker_id, w.repo);
      delete state.workers[m.worker_id]; saveState();
      send({ t: 'worker.destroyed', worker_id: m.worker_id });
    }
    else if (m.t === 'dispatch') runTask(m).catch(e => { busy.delete(m.worker_id); send({ t: 'result', ticket_id: m.ticket.id, worker_id: m.worker_id, result: { ok: false, summary: `node 异常：${e.message}` } }); });
    else if (m.t === 'cancel') {
      cancelled.add(m.ticket_id);
      const child = procs.get(m.worker_id);
      const w = state.workers[m.worker_id];
      console.log(`[node] 取消单 ${m.ticket_id}${child ? '，终止 worker 进程组' : '（无活跃进程）'}`);
      if (child) {
        killGroup(child, 'SIGTERM');
        const hard = setTimeout(() => killGroup(child, 'SIGKILL'), 10_000);
        child.once('close', () => { clearTimeout(hard); cleanupWorker(m.worker_id, w); });
      } else {
        cleanupWorker(m.worker_id, w);
      }
    }
    else if (m.t === 'room.roster') dialRoster(m.room_id, m.members || []);
    else if (m.t === 'room.notify') console.log(`[node] 雁鸣(兜底转发) ${m.room_id} → ${m.worker_id}:`, JSON.stringify(m.message).slice(0, 160));
  });
  ws.on('close', () => { clearInterval(hbTimer); console.log(`[node] 断开，${backoff / 1000}s 后重连`); setTimeout(connect, backoff); backoff = Math.min(backoff * 2, 30_000); });
  ws.on('error', (e) => console.error('[node] WS 错误：', e.message));
}

// 退出前先收掉在途 worker：它们是独立进程组，agent 直接退出会把它们留成孤儿——
// 继续烧模型额度，而且再也没人能取消。先 SIGTERM，最多等 10 秒，再 SIGKILL。
let shuttingDown = false;
function shutdown(signal) {
  if (shuttingDown) return;
  shuttingDown = true;
  const live = [...procs.values()];
  console.log(`[node] 收到 ${signal}，收掉 ${live.length} 个在途 worker 后退出`);
  for (const child of live) killGroup(child, 'SIGTERM');
  const finish = () => {
    for (const child of live) killGroup(child, 'SIGKILL');
    process.exit(0);
  };
  if (live.length === 0) { finish(); return; }
  setTimeout(finish, 10_000).unref();
  let left = live.length;
  for (const child of live) child.once('close', () => { if (--left <= 0) finish(); });
}
for (const sig of ['SIGTERM', 'SIGINT', 'SIGHUP']) process.on(sig, () => shutdown(sig));

console.log(`[node] 节点 ${NODE_ID} 启动 → ${CONTROLLER_URL}（平台 ${PLATFORMS.join(',') || '通用'}，容量 ${MAX_WORKERS}，活会话 ${Object.keys(state.workers).length}）`);
connect();
