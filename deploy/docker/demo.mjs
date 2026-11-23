#!/usr/bin/env node
// The demo assistant for the docker compose stack.
//
// It plays two roles that are separate in a real deployment: an external assistant that
// submits a request through the public API with its own key, and the operator who
// dispatches it (that would be a click in the console). Then it follows the ticket until
// the worker's fix is waiting for review, and prints what happened.
//
// Everything it uses is the real API; the only shortcut is that the assistant's key is
// declared in the compose file instead of being issued from the console.

const CONTROL = (process.env.CONTROL_URL || 'http://control:7800').replace(/\/+$/, '');
const TOKEN = process.env.SKEIN_TOKEN || '';
const KEY = process.env.SKEIN_ASSISTANT_KEY || '';
const PLATFORM = process.env.PLATFORM || 'demo';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function say(line = '') {
  console.log(line);
}

async function call(method, path, body, headers = {}) {
  const response = await fetch(`${CONTROL}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  return { status: response.status, data };
}

async function waitFor(label, timeoutMs, probe) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = await probe();
    if (value) return value;
    await sleep(1000);
  }
  throw new Error(`timed out waiting for ${label}`);
}

async function main() {
  say('skein demo — waiting for the control plane');
  await waitFor('the control plane', 120_000, async () => {
    try {
      const { status } = await call('GET', '/health/ready');
      return status === 200;
    } catch {
      return false;
    }
  });

  say('waiting for the demo node to register');
  await waitFor('the demo node', 120_000, async () => {
    const { status, data } = await call('GET', '/internal/nodes', undefined, {
      'X-Skein-Token': TOKEN,
    });
    if (status !== 200) return false;
    return (data?.nodes || []).some(
      (node) => node.id === 'demo-node' && node.status === 'online'
    );
  });

  // 1. The assistant submits a request (idempotent: source + external_id).
  const externalId = `run-${Date.now()}`;
  say('');
  say('1) the assistant submits a ticket through the public API (/api/v1)');
  const created = await call(
    'POST',
    '/api/v1/tickets',
    {
      title: 'Fix add() in the demo app: it subtracts instead of adding',
      description:
        'calculator.py has `return a - b` in add(). It should add. Please fix it and leave a note.',
      source: 'demo',
      external_id: externalId,
      requester: 'demo:visitor',
      category: 'bug',
      severity: 'normal',
      labels: ['demo'],
    },
    { Authorization: `Bearer ${KEY}` }
  );
  if (created.status !== 201 && created.status !== 200) {
    throw new Error(`submit failed: HTTP ${created.status} ${JSON.stringify(created.data)}`);
  }
  const ticket = created.data.ticket;
  say(`   ${ticket.code} created (${ticket.status})`);

  // 2. The operator dispatches it to the demo platform. Same API the console uses.
  say('2) the operator dispatches it to the "demo" platform');
  const dispatched = await call(
    'POST',
    `/internal/tickets/${encodeURIComponent(ticket.id)}/dispatch?platform=${encodeURIComponent(PLATFORM)}`,
    undefined,
    { 'X-Skein-Token': TOKEN }
  );
  if (dispatched.status !== 200 || dispatched.data?.ok !== true) {
    throw new Error(`dispatch failed: HTTP ${dispatched.status} ${JSON.stringify(dispatched.data)}`);
  }
  say(`   dispatched to node ${dispatched.data.node_id}`);

  // 3. Follow it. A healthy worker commits on its branch and reports back; the ticket then
  //    waits for a human in `review`. Nothing merges itself.
  say('3) waiting for the worker (the mock agent) to report back');
  let last = '';
  const final = await waitFor('the ticket to reach review', 300_000, async () => {
    const { data } = await call('GET', `/api/v1/tickets/${encodeURIComponent(ticket.id)}`, undefined, {
      Authorization: `Bearer ${KEY}`,
    });
    const state = data?.ticket?.status || '';
    if (state !== last) {
      say(`   status: ${state}`);
      last = state;
    }
    return ['review', 'done', 'rejected', 'failed'].includes(state) ? data.ticket : null;
  });

  say('');
  say(`done: ${final.code} is now "${final.status}" (public status: ${final.public_status})`);
  say(`console: ${CONTROL.replace('control', 'localhost')}/console/#/tickets/${final.id}`);
  say('');
  say('That is the whole loop: ticket → dispatch → worker commit → human review.');
  say('Nothing merged itself: accepting the ticket is a click in the console.');

  if (final.status !== 'review' && final.status !== 'done') {
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(`demo failed: ${error.message}`);
  process.exitCode = 1;
});
