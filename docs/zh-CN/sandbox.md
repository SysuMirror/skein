# 沙箱：让 worker 不能把整机拖垮

> 2026-11-18 · 基于宿主机实测（systemd 249.11 / cgroup v2 unified / Ubuntu 22.04）
> 对应实现：`services/node-agent/sandbox.mjs`。**默认不启用**（`SANDBOX_ENABLED=1` 且宿主完成一次性准备后才生效）；
> 不启用时 worker 直接以节点用户运行，隔离边界是节点/容器本身。

## 一、结论

用**专用低权用户 + systemd 瞬态 service + cgroup v2 + 挂载保护 + nft 端口白名单**。

- 用 `systemd-run --unit`（service），**不要用 `--scope`**：`ProtectSystem` 这类沙箱项只在 service 上生效。
- worker 以 `agent` 用户运行，工作目录限定在 `/srv/agent/jobs/<job>`。
- 资源、文件系统、系统调用、网络四层限制，见下方配额。
- 高保障备选：宿主机 docker 已注册 `runsc`，可 `docker run --runtime=runsc` 或 `runsc do` 脱离 k8s 单跑。

## 二、一次性准备（在节点上执行）

```bash
useradd -r -m -d /srv/agent -s /bin/bash agent
install -d -o agent -g agent -m 700 /srv/agent/jobs
install -d -o agent -g agent -m 700 /srv/agent/jobs/<job>/{.home,.npm}
```

**工作目录不要放根盘。** 根盘水位很高，必须放在独立盘、loop 镜像或 xfs project quota 上；否则写满会连坐同盘的生产服务。

网络白名单（nft，按 cgroup 匹配 unit）：

```bash
nft add table inet agent
nft add chain inet agent out '{ type filter hook output priority 0; }'
nft add chain inet agent job
nft add rule inet agent out socket cgroupv2 level 2 "system.slice/agent-<job>.service" jump job
nft add rule inet agent job oifname "lo" tcp dport 8080 accept
nft add rule inet agent job udp dport 53 accept
nft add rule inet agent job tcp dport {80,443} ip daddr != {10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.0/8} accept
nft add rule inet agent job drop
```

## 三、默认配额

| 项 | 值 |
| --- | --- |
| 内存 | 硬 3G，软 2.5G，swap 0 |
| CPU | 150%（1.5 核），weight 50 |
| PID | 384 |
| `/tmp` | 1G tmpfs（计入内存） |
| 单次运行 | 1 小时，30 秒收尾 |
| IO | 读 50M/s，写 20M/s |
| 同机并发 | 1 个（宿主已被生产占用大半） |

## 四、这套做不到什么

1. 共享内核，没有 gVisor；内核/LSM 漏洞可逃逸。
2. 能读所有 world-readable 文件（`/etc`、`/var/log` 等），`ProtectHome` 只挡 home。
3. 端口白名单放行了的端口上可以发任意 HTTP 请求，nft 看不懂应用层。
4. 没有磁盘容量/inode 配额，写满工作盘会连坐同盘。
5. 放行公网 443 后可以把读到的数据外传；防不了侧信道。
6. `SystemCallFilter` 若与 Node 冲突，先去掉它定位；**不要开 `MemoryDenyWriteExecute`**，会杀 V8 JIT。

要抗内核漏洞只能上 `runsc`，但那需要拉镜像，根盘水位高时需先腾空间。

## 五、止损三条

1. 冻结取证：`systemctl freeze agent-<job>`，`systemd-cgtop -m` 看内存/CPU/任务。
2. 杀整组：`systemctl kill -s KILL --kill-who=all agent-<job>`，再 `stop`、`reset-failed`。
3. 看伤情：`cat /sys/fs/cgroup/system.slice/agent-<job>.service/memory.events`（看 oom_kill）、`journalctl -u agent-<job> -n 200`、`df -h /`。

## 六、在代码里的位置

- `services/node-agent/sandbox.mjs`：构造 `systemd-run` 参数；`SANDBOX_ENABLED=1` 才启用。
- 未启用时行为不变（直接 spawn `claude`），用于过渡期。
- 启用前必须完成第二节的一次性准备，否则 worker 会因权限或路径问题起不来。
