// 沙箱：把 worker 放进 systemd 瞬态 service，四层限制（资源/文件/系统调用/网络）。
// 默认不启用；SANDBOX_ENABLED=1 且宿主完成一次性准备后才生效。
import { execFileSync } from 'node:child_process';

export const DEFAULT_LIMITS = {
  memoryMax: '3G',
  memoryHigh: '2500M',
  cpuQuota: '150%',
  tasksMax: 384,
  runtimeSec: 3600,
};

export function unitName(prefix, workerId) {
  const safe = String(workerId || 'worker').toLowerCase().replace(/[^a-z0-9-]/g, '-').slice(0, 40);
  return `${prefix}-${safe}`;
}

export function buildSandboxArgs({ unit, workdir, home, command, args = [], limits = DEFAULT_LIMITS }) {
  return [
    `--unit=${unit}`,
    '--uid=agent',
    '--gid=agent',
    `--working-directory=${workdir}`,
    '--pipe',
    '--wait',
    '--collect',
    '-p', 'Type=exec',
    '-p', `RuntimeMaxSec=${limits.runtimeSec}`,
    '-p', 'TimeoutStopSec=30',
    '-p', 'KillMode=control-group',
    '-p', 'MemoryAccounting=yes',
    '-p', `MemoryMax=${limits.memoryMax}`,
    '-p', `MemoryHigh=${limits.memoryHigh}`,
    '-p', 'MemorySwapMax=0',
    '-p', 'CPUAccounting=yes',
    '-p', `CPUQuota=${limits.cpuQuota}`,
    '-p', 'TasksAccounting=yes',
    '-p', `TasksMax=${limits.tasksMax}`,
    '-p', 'ProtectSystem=strict',
    '-p', 'ProtectHome=yes',
    '-p', `ReadWritePaths=${workdir}`,
    ...(home && home !== workdir ? ['-p', `ReadWritePaths=${home}`] : []),
    '-p', 'TemporaryFileSystem=/tmp:size=1G,mode=1777',
    '-p', 'PrivateIPC=yes',
    '-p', 'PrivateDevices=yes',
    '-p', 'NoNewPrivileges=yes',
    '-p', 'CapabilityBoundingSet=',
    '-p', 'RestrictNamespaces=yes',
    '-p', 'LockPersonality=yes',
    '-p', 'UMask=0077',
    '-p', 'ProtectKernelTunables=yes',
    '-p', 'ProtectKernelModules=yes',
    '-p', 'ProtectControlGroups=yes',
    '-p', 'ProtectClock=yes',
    '-p', 'ProtectHostname=yes',
    '-p', 'RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6',
    '-E', `HOME=${home || workdir}`,
    '--',
    command,
    ...args,
  ];
}

export function sandboxSupported(exec = execFileSync) {
  if (process.platform !== 'linux') return false;
  try {
    exec('systemd-run', ['--version'], { stdio: 'ignore' });
    return true;
  } catch {
    return false;
  }
}
