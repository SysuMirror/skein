import { test } from 'node:test';
import assert from 'node:assert/strict';

import { buildSandboxArgs, sandboxSupported, unitName, DEFAULT_LIMITS } from '../sandbox.mjs';

test('unit name is sanitized', () => {
  assert.equal(unitName('agent-n1', 'w-ABC_123/x'), 'agent-n1-w-abc-123-x');
  assert.equal(unitName('agent-n1', ''), 'agent-n1-worker');
});

test('sandbox args carry resource and filesystem limits', () => {
  const args = buildSandboxArgs({
    unit: 'agent-n1-w1',
    workdir: '/srv/agent/jobs/w1',
    home: '/srv/agent/jobs/w1/.home',
    command: 'claude',
    args: ['-p', 'hi'],
  });
  const text = args.join(' ');
  assert.match(text, /--unit=agent-n1-w1/);
  assert.match(text, /--uid=agent/);
  assert.match(text, /--working-directory=\/srv\/agent\/jobs\/w1/);
  assert.match(text, /MemoryMax=3G/);
  assert.match(text, /CPUQuota=150%/);
  assert.match(text, /TasksMax=384/);
  assert.match(text, /ProtectSystem=strict/);
  assert.match(text, /ReadWritePaths=\/srv\/agent\/jobs\/w1/);
  assert.match(text, /RuntimeMaxSec=3600/);
  assert.match(text, /HOME=\/srv\/agent\/jobs\/w1\/\.home/);
  assert.equal(args[args.length - 4], '--');
  assert.equal(args[args.length - 3], 'claude');
  assert.deepEqual(args.slice(-2), ['-p', 'hi']);
});

test('limits can be overridden', () => {
  const args = buildSandboxArgs({
    unit: 'u',
    workdir: '/w',
    command: 'c',
    limits: { ...DEFAULT_LIMITS, memoryMax: '1G', runtimeSec: 60 },
  });
  const text = args.join(' ');
  assert.match(text, /MemoryMax=1G/);
  assert.match(text, /RuntimeMaxSec=60/);
});

test('sandboxSupported returns false without systemd-run', () => {
  const failing = () => { throw new Error('missing'); };
  assert.equal(sandboxSupported(failing), false);
});
