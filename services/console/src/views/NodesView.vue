<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { fetchNodes, type NodeRecord } from "../api";
import EmptyState from "../components/EmptyState.vue";
import ErrorBar from "../components/ErrorBar.vue";
import NodeCard from "../components/NodeCard.vue";
import NodeDetailDrawer from "../components/NodeDetailDrawer.vue";

/** 心跳超过该秒数、且状态不是 offline 时，只标「疑似离线」。 */
const HEARTBEAT_STALE_S = 45;
const POLL_MS = 10000;

interface NodeCardModel {
  key: string;
  title: string;
  nodeId: string;
  status: string;
  statusLabel: string;
  statusCode: string;
  host: string;
  platforms: string[];
  platformsInvalid: boolean;
  capacity: string;
  heartbeat: string;
  heartbeatStale: boolean;
  heartbeatTitle: string;
  statusTitle: string;
  online: boolean;
}

const nodes = ref<NodeRecord[]>([]);
const route = useRoute();
const router = useRouter();
const selectedNodeId = ref<string | null>(null);
const loaded = ref(false);
const loading = ref(true);
const error = ref("");
const nowSec = ref(Date.now() / 1000);

let timer: number | undefined;
let disposed = false;
let requestSeq = 0;

function formatUnix(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return "—";
  const date = new Date(seconds * 1000);
  if (Number.isNaN(date.getTime())) return "—";
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function formatAge(age: number | null): string {
  if (age == null || !Number.isFinite(age)) return "";
  if (age < -1) return "时钟超前";
  const seconds = Math.max(0, Math.floor(age));
  if (seconds < 5) return "刚刚";
  if (seconds < 60) return `${seconds} 秒前`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
}

function heartbeatAge(lastSeen: number, now: number): number | null {
  if (typeof lastSeen !== "number" || !Number.isFinite(lastSeen) || lastSeen <= 0) return null;
  if (!Number.isFinite(now)) return null;
  return now - lastSeen;
}

function tagText(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (value == null) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return "";
  }
}

function parsePlatforms(raw: unknown): { tags: string[]; invalid: boolean } {
  if (Array.isArray(raw)) {
    return { tags: raw.map(tagText).filter((tag) => tag.length > 0), invalid: false };
  }
  if (typeof raw !== "string") {
    return { tags: [], invalid: raw != null && raw !== "" };
  }
  const text = raw.trim();
  if (!text) return { tags: [], invalid: false };
  try {
    const value: unknown = JSON.parse(text);
    if (Array.isArray(value)) {
      return { tags: value.map(tagText).filter((tag) => tag.length > 0), invalid: false };
    }
    if (value == null) return { tags: [], invalid: false };
    const single = tagText(value);
    return { tags: single ? [single] : [], invalid: false };
  } catch {
    return { tags: [text], invalid: true };
  }
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function zhStatus(raw: string): string {
  switch (raw.trim().toLowerCase()) {
    case "online":
      return "在线";
    case "offline":
      return "离线";
    case "running":
      return "进行中";
    case "failed":
    case "error":
      return "失败";
    case "done":
    case "succeeded":
      return "完成";
    case "success":
    case "ok":
      return "成功";
    case "pending":
      return "待处理";
    case "idle":
      return "空闲";
    case "busy":
      return "忙碌";
    case "creating":
      return "创建中";
    case "assigned":
      return "已派";
    case "staged":
      return "待派";
    case "review":
      return "待验收";
    case "rejected":
      return "驳回";
    case "cancelled":
    case "canceled":
      return "已取消";
    case "queued":
      return "排队";
    case "active":
      return "活跃";
    case "approved":
      return "已通过";
    case "warning":
    case "warn":
      return "警告";
    case "unauthorized":
      return "未授权";
    default:
      return raw.trim();
  }
}

function toCard(node: NodeRecord, index: number, now: number): NodeCardModel {
  const name = asText(node.name).trim();
  const nodeId = asText(node.id);
  const rawStatus = asText(node.status).trim();
  const normalized = rawStatus.toLowerCase();
  const offline = normalized === "offline";
  const age = heartbeatAge(node.last_seen, now);
  const fresh = age != null && age <= HEARTBEAT_STALE_S;
  let status = rawStatus || "neutral";
  let statusLabel = rawStatus || "未知";
  let heartbeatStale = false;
  let online = false;
  if (offline) {
    status = "offline";
    statusLabel = "离线";
  } else if (!fresh) {
    status = "warn";
    statusLabel = "疑似离线";
    heartbeatStale = true;
  } else if (normalized === "online") {
    status = "online";
    statusLabel = "在线";
    online = true;
  } else if (rawStatus) {
    statusLabel = zhStatus(rawStatus);
  }
  const stamp = formatUnix(node.last_seen);
  const relative = formatAge(age);
  const heartbeat = stamp === "—" ? "—" : relative ? `${stamp} ${relative}` : stamp;
  const heartbeatTitle = heartbeatStale
    ? `已超过 ${HEARTBEAT_STALE_S} 秒没有心跳，记录状态仍不是 offline`
    : "";
  const statusTitle = heartbeatStale
    ? `记录状态 ${rawStatus || "空"}。心跳超过 ${HEARTBEAT_STALE_S} 秒，不用离线的红色。`
    : rawStatus && statusLabel !== rawStatus
      ? rawStatus
      : "";
  const parsed = parsePlatforms(node.platforms);
  const capacity = Number.isFinite(node.max_workers) ? String(node.max_workers) : "—";
  return {
    key: `${nodeId || "node"}-${index}`,
    title: name || nodeId || "未命名节点",
    nodeId: nodeId || "—",
    status,
    statusLabel,
    statusCode: heartbeatStale && rawStatus ? rawStatus : "",
    host: asText(node.host).trim() || "—",
    platforms: parsed.tags,
    platformsInvalid: parsed.invalid,
    capacity,
    heartbeat,
    heartbeatStale,
    heartbeatTitle,
    statusTitle,
    online,
  };
}

const cards = computed(() => nodes.value.map((node, index) => toCard(node, index, nowSec.value)));
const selectedNode = computed(
  () => nodes.value.find((node) => node.id === selectedNodeId.value) ?? null,
);

function selectNode(id: string): void {
  selectedNodeId.value = id;
  if (route.params.id !== id) {
    void router.push(`/nodes/${encodeURIComponent(id)}`);
  }
}

function closeNode(): void {
  void router.replace("/nodes");
}

function syncNodeRoute(): void {
  const raw = route.params.id;
  selectedNodeId.value = typeof raw === "string" ? raw : null;
}

watch(() => route.params.id, syncNodeRoute, { immediate: true });
const onlineCount = computed(() => cards.value.filter((card) => card.online).length);
const countText = computed(() => (loaded.value ? `${onlineCount.value} / ${nodes.value.length}` : "—"));

function errorMessage(err: unknown): string {
  if (err instanceof Error && err.message) return err.message;
  return "加载节点失败";
}

async function load() {
  const seq = ++requestSeq;
  if (!loaded.value) loading.value = true;
  try {
    const body = await fetchNodes();
    if (disposed || seq !== requestSeq) return;
    if (!body || !Array.isArray(body.nodes)) {
      throw new Error("节点响应缺少 nodes");
    }
    nowSec.value = Date.now() / 1000;
    nodes.value = body.nodes;
    error.value = "";
    loaded.value = true;
  } catch (err) {
    if (disposed || seq !== requestSeq) return;
    nowSec.value = Date.now() / 1000;
    error.value = errorMessage(err);
  } finally {
    if (!disposed && seq === requestSeq) loading.value = false;
  }
}

onMounted(() => {
  void load();
  timer = window.setInterval(() => {
    void load();
  }, POLL_MS);
});

onBeforeUnmount(() => {
  disposed = true;
  if (timer !== undefined) window.clearInterval(timer);
});
</script>

<template>
  <section class="page" aria-labelledby="nodes-heading">
    <header class="page-head">
      <h2 id="nodes-heading" class="page__title">节点</h2>
      <div class="page-head__actions">
        <p class="count" title="在线数 / 总数。在线指状态为 online，且最近心跳不超过 45 秒">
          <span class="muted">在线</span>
          <span class="mono">{{ countText }}</span>
        </p>
        <button type="button" class="btn" @click="load">刷新</button>
      </div>
    </header>

    <ErrorBar v-if="error" :message="error" @retry="load" />

    <p v-if="loading && !loaded" class="sr">正在加载节点</p>
    <div v-if="loading && !loaded" class="cards" aria-busy="true">
      <div v-for="index in 3" :key="index" class="skeleton" aria-hidden="true">
        <span></span>
        <span></span>
        <span></span>
        <span></span>
      </div>
    </div>
    <EmptyState
      v-else-if="loaded && nodes.length === 0"
      title="尚无节点接入"
      description="在目标机器部署 node agent，拨入后会自动出现在这里。"
    />
    <div v-else-if="cards.length" class="cards">
      <NodeCard
        v-for="card in cards"
        :key="card.key"
        :title="card.title"
        :node-id="card.nodeId"
        :status="card.status"
        :status-label="card.statusLabel"
        :status-code="card.statusCode"
        :host="card.host"
        :platforms="card.platforms"
        :platforms-invalid="card.platformsInvalid"
        :capacity="card.capacity"
        :heartbeat="card.heartbeat"
        :heartbeat-stale="card.heartbeatStale"
        :heartbeat-title="card.heartbeatTitle"
        :status-title="card.statusTitle"
        @select="selectNode"
      />
    </div>
    <NodeDetailDrawer
      v-if="selectedNode"
      :node="selectedNode"
      @close="closeNode"
    />
  </section>
</template>

<style scoped>
.page {
  container-type: inline-size;
  position: relative;
  width: 100%;
  min-width: 0;
  max-width: 100%;
  overflow-x: clip;
}

.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
  margin-bottom: 8px;
  padding-bottom: 8px;
  background-image: linear-gradient(90deg, rgba(79, 216, 235, 0.4), transparent 72%);
  background-repeat: no-repeat;
  background-position: left bottom;
  background-size: 100% 1px;
}

.page-head .page__title {
  min-width: 0;
  margin: 0;
  color: var(--muted);
  font-weight: 500;
  letter-spacing: 0.22em;
}

.page-head__actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 0 0 auto;
  min-width: 0;
}

.count {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin: 0;
  white-space: nowrap;
}

.count .mono {
  color: var(--cyan);
  font-size: 16px;
  font-weight: 700;
  text-shadow: 0 0 12px rgba(79, 216, 235, 0.55);
}

.btn {
  position: relative;
  flex: 0 0 auto;
  padding: 4px 8px 7px;
  background: transparent;
  border: none;
  color: var(--text);
  letter-spacing: 0.12em;
  cursor: pointer;
}

.btn::after {
  content: "";
  position: absolute;
  right: 10%;
  bottom: 2px;
  left: 10%;
  height: 1px;
  background: linear-gradient(90deg, transparent, currentColor, transparent);
  opacity: 0.85;
  transform: scaleX(0);
  transition: transform 0.22s ease;
}

.btn:hover,
.btn:focus-visible {
  color: var(--cyan);
  text-shadow: 0 0 8px rgba(79, 216, 235, 0.75);
}

.btn:hover::after,
.btn:focus-visible::after {
  transform: scaleX(1);
}

.btn:focus-visible {
  outline: 1px solid var(--cyan);
  outline-offset: 2px;
}

.cards {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 4px 28px;
  min-width: 0;
  padding-left: 4px;
}

.skeleton {
  min-height: 132px;
  padding: 14px 8px 10px 16px;
  border: none;
  background: transparent;
  box-shadow: inset 2px 0 0 rgba(79, 216, 235, 0.28);
}

.skeleton span {
  display: block;
  width: 88%;
  height: 1px;
  margin: 14px 0;
  background: linear-gradient(90deg, rgba(125, 137, 184, 0.55), transparent 80%);
}

.skeleton span:first-child {
  width: 46%;
  height: 2px;
  background: linear-gradient(90deg, rgba(79, 216, 235, 0.55), transparent 80%);
}

.skeleton span:nth-child(3) {
  width: 72%;
}

.sr {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

@container (max-width: 920px) {
  .cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@container (max-width: 520px) {
  .cards {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
