import assert from 'node:assert/strict';
import { test } from 'node:test';

import { workerEnvironment } from '../worker-env.mjs';

test('worker 环境剔除控制面令牌', () => {
  const env = workerEnvironment({
    PATH: '/usr/bin',
    HOME: '/home/cloud',
    SKEIN_TOKEN: 'secret',
    AGENT_TOKEN: 'secret',
    CONTROLLER_URL: 'wss://example',
    SKEIN_CONTROL_URL: 'https://example',
  });
  assert.deepEqual(env, { PATH: '/usr/bin', HOME: '/home/cloud' });
});

test('worker 环境保留 LLM 与普通变量', () => {
  const env = workerEnvironment({ PATH: '/bin', LANG: 'C.UTF-8', ANTHROPIC_BASE_URL: 'x' });
  assert.equal(env.ANTHROPIC_BASE_URL, 'x');
  assert.equal(env.LANG, 'C.UTF-8');
});
