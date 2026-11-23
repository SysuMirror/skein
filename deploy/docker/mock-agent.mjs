#!/usr/bin/env node
// A stand-in coding agent for the docker compose demo. It speaks the same command line and
// stream-json protocol the node agent uses for Claude Code (`-p <prompt> --output-format
// stream-json --verbose … [--resume <id>]`), but it never calls a model: for a ticket it
// makes a small, honest commit on a fix branch so the whole pipeline can be watched end to
// end — dispatch → worker → report → draft PR / review.
//
// This exists so the demo works offline. It is not a replacement for a real agent; see
// docs/worker-runtime.md for what a production node runs.

import { execFileSync } from 'node:child_process';
import { appendFileSync, mkdirSync } from 'node:fs';
import { randomBytes } from 'node:crypto';

const args = process.argv.slice(2);
const promptIndex = args.indexOf('-p');
const prompt = promptIndex >= 0 ? String(args[promptIndex + 1] || '') : '';
const resumeIndex = args.indexOf('--resume');
const resumed = resumeIndex >= 0 ? String(args[resumeIndex + 1] || '') : '';
const sessionId = resumed || `mock-${randomBytes(6).toString('hex')}`;

function emit(message) {
  process.stdout.write(JSON.stringify(message) + '\n');
}

function text(value) {
  emit({ type: 'assistant', message: { content: [{ type: 'text', text: value }] } });
}

function git(...argv) {
  return execFileSync('git', argv, { encoding: 'utf8' }).trim();
}

function finish(summary, subtype = 'success') {
  emit({ type: 'result', subtype, result: summary, session_id: sessionId });
  process.exit(subtype === 'success' ? 0 : 1);
}

emit({ type: 'system', subtype: 'init', session_id: sessionId });

// Initialisation run (no --resume): look around, say "ready", touch nothing.
if (!resumed) {
  let files = '';
  try {
    files = git('ls-files').split('\n').slice(0, 20).join(', ');
  } catch {
    files = '(not a git checkout)';
  }
  text(`就绪。仓库文件：${files || '(空)'}`);
  finish('就绪');
}

// Ticket run: pick the branch out of the prompt, make one small commit, summarise.
// 分支名与工单编号后面紧跟中文说明，只取 ASCII 字符
const branchMatch = prompt.match(/git checkout -b ([A-Za-z0-9._\/-]+)/);
const codeMatch = prompt.match(/需求单\s+([A-Z]{2}-[A-Za-z0-9]+)/);
const branch = branchMatch ? branchMatch[1] : `fix/mock-${Date.now().toString(36)}`;
const code = codeMatch ? codeMatch[1] : 'ticket';

try {
  const current = git('branch', '--show-current');
  if (current !== branch) {
    try {
      git('checkout', '-b', branch);
    } catch {
      git('checkout', branch);
    }
  }
  text(`在分支 ${branch} 上处理 ${code}：先看仓库，再做一个最小的修复。`);
  const note = `# ${code}\n\nHandled by the mock agent of the docker compose demo at ${new Date().toISOString()}.\n`;
  const noteDir = 'notes';
  mkdirSync(noteDir, { recursive: true });
  appendFileSync(`${noteDir}/${code}.md`, note);
  git('add', `${noteDir}/${code}.md`);
  git(
    '-c',
    'user.name=Skein mock agent',
    '-c',
    'user.email=mock@example.com',
    'commit',
    '-m',
    `${code}: note from the demo worker`
  );
  const commits = git('log', '--oneline', '-1');
  text(`已提交：${commits}`);
  finish(`Mock fix for ${code} on ${branch}. Committed: ${commits}.`);
} catch (error) {
  finish(`Mock agent could not complete ${code}: ${error.message}`, 'error');
}
