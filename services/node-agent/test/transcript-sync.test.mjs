import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { findTranscript, TranscriptSync } from '../transcript-sync.mjs';

function homeWithTranscript(sessionId, body = '{"turn":0}\n') {
  const home = mkdtempSync(join(tmpdir(), 'skein-home-'));
  const dir = join(home, '.claude', 'projects', 'proj');
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, `${sessionId}.jsonl`), body);
  return home;
}

function recorder(responses) {
  const calls = [];
  let i = 0;
  return {
    calls,
    fetch: async (url, options) => {
      calls.push({ url, options });
      const r = responses[Math.min(i++, responses.length - 1)];
      return { ok: r === 200, status: r };
    },
  };
}

test('findTranscript locates the session file', () => {
  const home = homeWithTranscript('sess-1');
  assert.match(findTranscript(home, 'sess-1'), /sess-1\.jsonl$/);
  assert.equal(findTranscript(home, 'missing'), null);
});

test('syncTurn posts base64 transcript', async () => {
  const home = homeWithTranscript('s1', 'hello');
  const rec = recorder([200]);
  const sync = new TranscriptSync({
    url: 'http://control', token: 'tok', queuePath: join(home, 'q.jsonl'), fetchImpl: rec.fetch, homeDir: home,
  });
  const result = await sync.syncTurn({ sessionId: 's1', turnIndex: 0, headSha: 'abc', branch: 'fix/x' });
  assert.equal(result.ok, true);
  const body = JSON.parse(rec.calls[0].options.body);
  assert.equal(body.session_id, 's1');
  assert.equal(body.turn_index, 0);
  assert.equal(Buffer.from(body.data, 'base64').toString(), 'hello');
  assert.equal(body.head_sha, 'abc');
  assert.equal(rec.calls[0].options.headers['X-Skein-Token'], 'tok');
});

test('failed upload is queued and flushed later', async () => {
  const home = homeWithTranscript('s2', 'later');
  const queuePath = join(home, 'q.jsonl');
  const failing = recorder([500]);
  const sync = new TranscriptSync({ url: 'http://control', token: 't', queuePath, fetchImpl: failing.fetch, homeDir: home });
  const result = await sync.syncTurn({ sessionId: 's2', turnIndex: 1 });
  assert.equal(result.ok, false);
  assert.equal(result.queued, true);
  assert.ok(existsSync(queuePath));

  const ok = recorder([200]);
  sync.fetch = ok.fetch;
  const flushed = await sync.flush();
  assert.equal(flushed.sent, 1);
  assert.equal(flushed.left, 0);
  assert.equal(readFileSync(queuePath, 'utf8'), '');
});

test('409 counts as already synced', async () => {
  const home = homeWithTranscript('s3');
  const rec = recorder([409]);
  const sync = new TranscriptSync({ url: 'http://c', token: 't', queuePath: join(home, 'q.jsonl'), fetchImpl: rec.fetch, homeDir: home });
  const result = await sync.syncTurn({ sessionId: 's3', turnIndex: 0 });
  assert.equal(result.ok, true);
});

test('missing transcript is reported, not queued', async () => {
  const home = mkdtempSync(join(tmpdir(), 'skein-home-'));
  const sync = new TranscriptSync({ url: 'http://c', token: 't', queuePath: join(home, 'q.jsonl'), fetchImpl: recorder([200]).fetch, homeDir: home });
  const result = await sync.syncTurn({ sessionId: 'nope', turnIndex: 0 });
  assert.deepEqual(result, { ok: false, reason: 'no_transcript' });
  assert.equal(existsSync(join(home, 'q.jsonl')), false);
});
