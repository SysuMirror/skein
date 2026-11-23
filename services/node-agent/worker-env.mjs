// worker 进程的环境白名单：控制面令牌只属于 node-agent，不进 cc。
const PRIVATE_KEYS = new Set([
  'SKEIN_TOKEN',
  'AGENT_TOKEN',
  'CONTROLLER_URL',
  'SKEIN_CONTROL_URL',
]);

export function workerEnvironment(base) {
  return Object.fromEntries(
    Object.entries(base).filter(([key]) => !PRIVATE_KEYS.has(key)),
  );
}
