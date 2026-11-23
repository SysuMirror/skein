<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { fetchSummary, fetchTopology, type Summary, type Topology } from "../api";
import ErrorBar from "../components/ErrorBar.vue";
import StatusTag from "../components/StatusTag.vue";
import TopologyBoard from "../components/TopologyBoard.vue";
import { useLiveStore, type Snapshot } from "../live";

const POLL_MS = 10000;
const DISPATCH_ROWS = 8;

const STATE_LABEL: Record<string, string> = {
  pending: "待执行",
  dispatched: "已派",
  created: "已建会话",
  done: "完成",
  failed: "失败",
  queued: "排队",
};

const live = useLiveStore();

const summary = ref<Summary | null>(null);
const topology = ref<Topology | null>(null);
const error = ref<string | null>(null);
const loading = ref(false);

let alive = true;
let seq = 0;
let timer = 0;

const byStatus = computed<Record<string, number>>(
  () => summary.value?.tickets.by_status ?? {},
);
const reviewCount = computed(() => byStatus.value.review ?? 0);
const activeCount = computed(() =>
  ["staged", "assigned", "running"].reduce((total, key) => total + (byStatus.value[key] ?? 0), 0),
);
const finishedCount = computed(() =>
  ["done", "rejected"].reduce((total, key) => total + (byStatus.value[key] ?? 0), 0),
);

function detailText(detail: unknown): string {
  if (detail == null) return "";
  if (typeof detail === "string") return detail;
  try {
    return JSON.stringify(detail);
  } catch {
    return String(detail);
  }
}

function formatStamp(ts: number): string {
  if (!Number.isFinite(ts)) return "—";
  const date = new Date(ts < 1e12 ? ts * 1000 : ts);
  if (Number.isNaN(date.getTime())) return "—";
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

const recentDispatches = computed(() => {
  const tickets = new Map<string, string>();
  for (const item of topology.value?.tickets ?? []) tickets.set(item.id, item.code);
  return [...(topology.value?.dispatches ?? [])]
    .sort((a, b) => b.updated_at - a.updated_at)
    .slice(0, DISPATCH_ROWS)
    .map((item) => ({
      id: item.id,
      code: tickets.get(item.ticket_id) ?? item.ticket_id.slice(0, 8),
      platform: item.platform || "—",
      node: item.node_id || "—",
      state: item.state,
      stateLabel: STATE_LABEL[item.state] ?? item.state,
      detail: detailText(item.detail),
      at: formatStamp(item.updated_at),
      failed: item.state === "failed",
    }));
});


function errorMessage(err: unknown): string {
  if (err instanceof Error && err.message) return err.message;
  return "网络错误";
}

async function load() {
  const token = ++seq;
  const initial = summary.value == null && topology.value == null;
  if (initial) loading.value = true;
  try {
    const [summaryResult, topologyResult] = await Promise.allSettled([
      fetchSummary(),
      fetchTopology(),
    ]);
    if (!alive || token !== seq) return;
    let nextError: string | null = null;
    if (summaryResult.status === "fulfilled") summary.value = summaryResult.value;
    else nextError = errorMessage(summaryResult.reason);
    if (topologyResult.status === "fulfilled") topology.value = topologyResult.value;
    else nextError = nextError ?? errorMessage(topologyResult.reason);
    error.value = nextError;
  } finally {
    if (alive && token === seq) loading.value = false;
  }
}

function applySnapshot(snap: Snapshot) {
  summary.value = snap.summary;
  topology.value = snap.topology;
  loading.value = false;
  error.value = null;
}

watch(
  () => live.snapshot,
  (snap) => {
    if (snap) applySnapshot(snap);
  },
);

onMounted(() => {
  if (live.snapshot) applySnapshot(live.snapshot);
  void load();
  timer = window.setInterval(() => {
    if (live.state !== "open") void load();
  }, POLL_MS);
});

onUnmounted(() => {
  alive = false;
  window.clearInterval(timer);
});
</script>

<template>
  <section class="page">
    <h2 class="page__title">
      总览
      <span v-if="live.state === 'open'" class="page__live">实时</span>
    </h2>
    <ErrorBar v-if="error" :message="error" @retry="load()" />
    <p v-if="loading && !summary && !topology" class="muted load">加载中</p>

    <div v-if="summary" class="metrics">
      <div class="metric metric--attention">
        <span class="metric__label">待你验收</span>
        <span class="metric__value mono">{{ reviewCount }}</span>
        <span class="metric__hint">需要人做决定</span>
      </div>
      <div class="metric">
        <span class="metric__label">进行中</span>
        <span class="metric__value mono">{{ activeCount }}</span>
        <span class="metric__hint">暂存 / 已派 / 执行中</span>
      </div>
      <div class="metric">
        <span class="metric__label">已结束</span>
        <span class="metric__value mono">{{ finishedCount }}</span>
        <span class="metric__hint">完成或驳回</span>
      </div>
      <div class="metrics__aside">
        <span>事件 <b class="mono">{{ summary.events.total }}</b></span>
        <span>会话 <b class="mono">{{ summary.sessions }}</b></span>
      </div>
    </div>

    <section v-if="recentDispatches.length" class="dispatches">
      <h3>最近派工</h3>
      <div class="scroll">
        <table class="dense">
          <thead>
            <tr>
              <th scope="col">时间</th>
              <th scope="col">工单</th>
              <th scope="col">平台</th>
              <th scope="col">节点</th>
              <th scope="col">状态</th>
              <th scope="col">说明</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="row in recentDispatches"
              :key="row.id"
              :class="{ 'is-failed': row.failed }"
            >
              <td class="mono">{{ row.at }}</td>
              <td class="mono">{{ row.code }}</td>
              <td class="mono">{{ row.platform }}</td>
              <td class="mono">{{ row.node }}</td>
              <td><status-tag :status="row.state" :label="row.stateLabel" /></td>
              <td>
                <span class="detail" :title="row.detail || undefined">{{ row.detail || "—" }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <TopologyBoard
      v-if="topology"
      :nodes="topology.nodes"
      :tickets="topology.tickets"
      :dispatches="topology.dispatches"
      :sessions="topology.sessions"
    />
  </section>
</template>

<style scoped>
.page {
  min-width: 0;
  max-width: 100%;
  overflow-x: clip;
}

.load {
  margin: 0 0 10px;
}

.page__live {
  margin-left: 8px;
  padding: 1px 6px;
  border: 1px solid var(--ok);
  color: var(--ok);
  font-size: 10px;
  letter-spacing: 0.16em;
  vertical-align: 2px;
}

.metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
  max-width: 100%;
  margin: 0 0 12px;
}

.metric {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px 10px;
  min-width: 0;
  max-width: 100%;
  padding: 6px 10px;
  background: var(--bg-elevated);
  border: 1px solid var(--line);
}

.metric__label {
  color: var(--muted);
}

.metric__value {
  font-size: 16px;
  font-weight: 600;
}

.metric__part {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}

.metric {
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  min-width: 132px;
  padding: 10px 14px;
}

.metric__value {
  font-size: 26px;
  line-height: 1.1;
}

.metric__hint {
  color: var(--muted);
  font-size: 11px;
}

.metric--attention .metric__value {
  color: var(--amber);
  text-shadow: 0 0 14px rgba(210, 153, 34, 0.45);
}

.metrics__aside {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-left: auto;
  color: var(--muted);
  font-size: 12px;
}

.metrics__aside b {
  margin-left: 4px;
  color: var(--text);
  font-weight: 600;
}

.dot {
  display: inline-block;
  flex: 0 0 auto;
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.dispatches {
  margin: 0 0 14px;
  padding: 8px 2px 8px 14px;
  border-left: 1px solid rgba(79, 216, 235, 0.35);
  box-shadow: -10px 0 16px -14px rgba(79, 216, 235, 0.85);
}

.dispatches h3 {
  margin: 0 0 8px;
  color: var(--cyan);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 2px;
}

.scroll {
  max-width: 100%;
  overflow-x: auto;
}

.dense {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: 12px;
}

.dense th,
.dense td {
  padding: 4px 8px;
  border-bottom: 1px solid rgba(125, 137, 184, 0.14);
  text-align: left;
  vertical-align: top;
  overflow-wrap: anywhere;
}

.dense th {
  color: var(--muted);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.08em;
}

.mono {
  color: #d5def8;
  font-family: var(--mono);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.detail {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dense tr.is-failed td {
  color: var(--bad);
}

.dense tr.is-failed .mono {
  color: var(--bad);
}
</style>
