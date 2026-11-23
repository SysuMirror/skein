<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import {
  fetchEvents,
  fetchSignals,
  fetchSummary,
  fetchTickets,
  fetchTopology,
  request,
  type ControlEvent,
  type Signal,
  type Summary,
  type Ticket,
  type Topology,
} from "../api";
import EmptyState from "../components/EmptyState.vue";
import ErrorBar from "../components/ErrorBar.vue";
import FlowRibbon, { type FlowStage } from "../components/FlowRibbon.vue";
import PulseDeck from "../components/PulseDeck.vue";
import { useLiveStore, type Snapshot } from "../live";
import { useConsoleStore } from "../store";

const live = useLiveStore();
const store = useConsoleStore();
// 只有能验收的角色才看得见通过 / 驳回按钮
const canOperate = computed(() => store.can("ticket.operate"));

const summary = ref<Summary | null>(null);
const topology = ref<Topology | null>(null);
const signals = ref<Signal[]>([]);
const tickets = ref<Ticket[]>([]);
const events = ref<ControlEvent[]>([]);
const loading = ref(true);
const error = ref<string | null>(null);
const acting = ref<string | null>(null);
let alive = true;
let timer: number | undefined;

const byStatus = computed(() => summary.value?.tickets.by_status ?? {});
const reviewCount = computed(() => byStatus.value.review ?? 0);
const doneCount = computed(() => byStatus.value.done ?? 0);
const rejectedCount = computed(() => byStatus.value.rejected ?? 0);
const activeTickets = computed(
  () =>
    (byStatus.value.staged ?? 0) +
    (byStatus.value.assigned ?? 0) +
    (byStatus.value.running ?? 0),
);

const reviewTickets = computed(() =>
  tickets.value
    .filter((ticket) => ticket.status === "review")
    .sort((a, b) => b.updated_at - a.updated_at),
);

const activeDispatches = computed(
  () =>
    topology.value?.dispatches.filter((item) =>
      ["dispatched", "created", "pending"].includes(item.state),
    ).length ?? 0,
);
const onlineNodes = computed(
  () => topology.value?.nodes.filter((node) => node.status === "online").length ?? 0,
);
const sessionCount = computed(() => topology.value?.sessions.length ?? 0);

const stalled = computed(() =>
  signals.value.filter((item) =>
    ["dispatch_timeout", "node_offline_with_work"].includes(item.kind),
  ),
);
const failures = computed(() =>
  signals.value.filter((item) => item.kind === "run_failed"),
);
const topSignals = computed(() => stalled.value.concat(failures.value).slice(0, 5));

const verdicts = computed(() => summary.value?.events.by_verdict ?? {});

const stages = computed<FlowStage[]>(() => [
  {
    key: "intake",
    label: "反馈",
    value: summary.value?.events.total ?? 0,
    sub: `忽略 ${verdicts.value.ignore ?? 0} · 记录 ${verdicts.value.record ?? 0}`,
    tone: "cyan",
    to: "/events",
  },
  {
    key: "admission",
    label: "准入",
    value: verdicts.value.process ?? 0,
    sub: "程序判定，不调模型",
    tone: "violet",
    to: "/platform",
  },
  {
    key: "ticket",
    label: "工单",
    value: summary.value?.tickets.total ?? 0,
    sub: `进行中 ${activeTickets.value}`,
    tone: "cyan",
    to: "/tickets",
  },
  {
    key: "dispatch",
    label: "派工",
    value: activeDispatches.value,
    sub: `${onlineNodes.value}/${topology.value?.nodes.length ?? 0} 节点在线`,
    tone: "amber",
    to: "/fleet",
  },
  {
    key: "deliver",
    label: "交付",
    value: reviewCount.value,
    sub: "等你验收",
    tone: "amber",
    to: "/tickets",
  },
  {
    key: "review",
    label: "验收",
    value: doneCount.value,
    sub: `驳回 ${rejectedCount.value}`,
    tone: "ok",
    to: "/tickets",
  },
]);

const recentDelivered = computed(() =>
  tickets.value
    .filter((ticket) => ["done", "rejected"].includes(ticket.status))
    .sort((a, b) => b.updated_at - a.updated_at)
    .slice(0, 4),
);

const feed = computed(() => events.value.slice(0, 8));

const ORIGIN: Record<string, string> = {
  forum_post: "论坛帖",
  forum_comment: "论坛评论",
  im: "群消息",
  rss: "订阅",
  manual: "手动",
};

const VERDICT: Record<string, { label: string; tone: string }> = {
  process: { label: "处理", tone: "ok" },
  record: { label: "记录", tone: "muted" },
  ignore: { label: "忽略", tone: "bad" },
};

function originLabel(origin: string): string {
  return ORIGIN[origin] ?? origin;
}

function verdictOf(event: ControlEvent) {
  return VERDICT[event.verdict] ?? { label: event.verdict, tone: "muted" };
}

function ago(ts: number): string {
  if (!ts) return "";
  const diff = Math.max(0, Date.now() / 1000 - ts);
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

function signalText(signal: Signal): string {
  switch (signal.kind) {
    case "dispatch_timeout":
      return `派单超时：工单 ${signal.ticket_id} 在节点 ${signal.node_id} 已等待 ${Math.round((signal.age_s ?? 0) / 60)} 分钟`;
    case "run_failed":
      return `作业失败：${signal.job} ${signal.day}`;
    case "node_offline_with_work":
      return `节点离线且有在途工单：${signal.node_id} / 工单 ${signal.ticket_id}`;
    default:
      return signal.kind;
  }
}

async function load() {
  loading.value = true;
  error.value = null;
  const [sum, topo, signalList, ticketList, eventList] = await Promise.allSettled([
    fetchSummary(),
    fetchTopology(),
    fetchSignals(),
    fetchTickets({ limit: 100 }),
    fetchEvents({ limit: 12 }),
  ]);
  if (!alive) return;
  if (sum.status === "fulfilled") summary.value = sum.value;
  if (topo.status === "fulfilled") topology.value = topo.value;
  if (signalList.status === "fulfilled") signals.value = signalList.value.signals;
  if (ticketList.status === "fulfilled") tickets.value = ticketList.value.tickets;
  if (eventList.status === "fulfilled") events.value = eventList.value.events;
  const failed = [sum, topo, signalList, ticketList, eventList].filter(
    (item) => item.status === "rejected",
  );
  error.value = failed.length
    ? failed
        .map((item) =>
          item.status === "rejected" && item.reason instanceof Error
            ? item.reason.message
            : "加载失败",
        )
        .join("；")
    : null;
  loading.value = false;
}

function applySnapshot(snap: Snapshot) {
  summary.value = snap.summary;
  topology.value = snap.topology;
  signals.value = snap.signals;
  tickets.value = snap.tickets;
  events.value = snap.events;
  loading.value = false;
  error.value = null;
}

watch(
  () => live.snapshot,
  (snap) => {
    if (snap) applySnapshot(snap);
  },
);

async function decide(ticket: Ticket, decision: "approve" | "reject") {
  if (acting.value) return;
  if (decision === "reject" && !window.confirm(`确认驳回 ${ticket.code}？状态将变为已驳回。`)) {
    return;
  }
  acting.value = ticket.id;
  try {
    await request(`/internal/tickets/${encodeURIComponent(ticket.id)}/review`, {
      method: "POST",
      body: JSON.stringify({ decision }),
    });
    await load();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "操作失败";
  } finally {
    acting.value = null;
  }
}

onMounted(() => {
  if (live.snapshot) applySnapshot(live.snapshot);
  void load();
  timer = window.setInterval(() => {
    if (live.state !== "open") void load();
  }, 15000);
});

onBeforeUnmount(() => {
  alive = false;
  if (timer !== undefined) window.clearInterval(timer);
});
</script>

<template>
  <section class="today">
    <header class="today__head">
      <div class="today__title">
        <h2>工作台</h2>
        <p class="muted">现在有什么要我处理 · 谁在干什么 · 卡在哪</p>
      </div>
      <div class="today__meta">
        <span v-if="live.state === 'open'" class="live-tag">实时</span>
        <span class="muted mono">{{ sessionCount }} 会话</span>
        <button type="button" class="ghost" :disabled="loading" @click="load">
          {{ loading ? "刷新中" : "刷新" }}
        </button>
      </div>
    </header>

    <error-bar v-if="error" :message="error" @retry="load" />

    <PulseDeck
      :nodes="topology?.nodes ?? []"
      :sessions="topology?.sessions ?? []"
      :tickets="tickets"
      :dispatches="topology?.dispatches ?? []"
      :review="reviewCount"
      :stalled="stalled.length + failures.length"
      :running="activeDispatches"
      :delivered="doneCount"
    />

    <FlowRibbon :stages="stages" :active="activeDispatches" />

    <div class="lanes">
      <article class="panel panel--review">
        <header class="panel__head">
          <h3>待你验收</h3>
          <span class="panel__count mono">{{ reviewCount }}</span>
          <router-link to="/tickets" class="panel__more">全部需求 →</router-link>
        </header>
        <div v-if="reviewTickets.length" class="rows">
          <div v-for="ticket in reviewTickets.slice(0, 6)" :key="ticket.id" class="row">
            <span class="mono code">{{ ticket.code }}</span>
            <span class="title">{{ ticket.title }}</span>
            <span class="muted stamp">{{ ago(ticket.updated_at) }}</span>
            <span v-if="canOperate" class="actions">
              <button
                type="button"
                class="mini"
                :disabled="acting !== null"
                @click="decide(ticket, 'approve')"
              >
                通过
              </button>
              <button
                type="button"
                class="mini danger"
                :disabled="acting !== null"
                @click="decide(ticket, 'reject')"
              >
                驳回
              </button>
            </span>
          </div>
        </div>
        <empty-state
          v-else
          title="没有待验收的工单"
          description="Worker 交付后会出现在这里；系统不会自行合入。"
        />
        <div v-if="recentDelivered.length" class="delivered">
          <span class="muted">最近交付</span>
          <span v-for="ticket in recentDelivered" :key="ticket.id" class="chip">
            <span class="mono">{{ ticket.code }}</span>
            <b :class="ticket.status === 'done' ? 'ok' : 'bad'">
              {{ ticket.status === "done" ? "已通过" : "已驳回" }}
            </b>
          </span>
        </div>
      </article>

      <article class="panel panel--stuck">
        <header class="panel__head">
          <h3>卡住的事</h3>
          <span class="panel__count panel__count--bad mono">{{ topSignals.length }}</span>
          <router-link to="/signals" class="panel__more">全部信号 →</router-link>
        </header>
        <div v-if="topSignals.length" class="rows">
          <div
            v-for="signal in topSignals"
            :key="`${signal.kind}-${signal.id}`"
            class="row row--signal"
          >
            <span class="dot" aria-hidden="true"></span>
            <span class="title">{{ signalText(signal) }}</span>
          </div>
        </div>
        <empty-state
          v-else
          title="没有卡住的派工"
          description="派工超时、作业失败、写回失败或节点离线会出现在这里。"
        />
      </article>

      <article class="panel panel--feed">
        <header class="panel__head">
          <h3>阵中动态</h3>
          <span class="panel__count panel__count--muted mono">{{ feed.length }}</span>
          <router-link to="/events" class="panel__more">事件流 →</router-link>
        </header>
        <ol v-if="feed.length" class="feed">
          <li v-for="event in feed" :key="event.id" class="feed__item">
            <span class="verdict" :class="`verdict--${verdictOf(event).tone}`">
              {{ verdictOf(event).label }}
            </span>
            <span class="feed__text">
              {{ originLabel(event.origin) }}
              <span class="mono muted">{{ event.origin_id }}</span>
            </span>
            <span class="muted stamp">{{ ago(event.created_at) }}</span>
          </li>
        </ol>
        <empty-state
          v-else
          title="还没有反馈进来"
          description="论坛帖、评论、群消息被采集后会在这里排成一条流。"
        />
      </article>
    </div>

    <p v-if="loading && !summary && !topology" class="muted loading">正在读取工作台…</p>
  </section>
</template>

<style scoped>
.today {
  min-width: 0;
}

.today__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.today__title h2 {
  margin: 0;
  color: var(--text);
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 0.14em;
}

.today__title p {
  margin: 4px 0 0;
  font-size: 12px;
}

.today__meta {
  display: flex;
  align-items: center;
  gap: 14px;
  font-size: 11px;
  letter-spacing: 0.1em;
}

.live-tag {
  padding: 1px 7px;
  border: 1px solid var(--ok);
  color: var(--ok);
  font-size: 10px;
  letter-spacing: 0.16em;
  text-shadow: 0 0 8px rgba(87, 217, 163, 0.5);
}

.ghost {
  padding: 4px 12px 6px;
  background: transparent;
  border: none;
  color: var(--muted);
  letter-spacing: 0.12em;
  cursor: pointer;
}

.ghost:hover:not(:disabled) {
  color: var(--cyan);
  text-shadow: 0 0 10px rgba(79, 216, 235, 0.65);
}

.lanes {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(0, 1fr) minmax(0, 1fr);
  gap: 12px;
  align-items: stretch;
}

@media (max-width: 1240px) {
  .lanes {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  }

  .panel--feed {
    grid-column: 1 / -1;
  }
}

@media (max-width: 820px) {
  .lanes {
    grid-template-columns: minmax(0, 1fr);
  }

  .panel--feed {
    grid-column: auto;
  }
}

.panel {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 12px 14px 10px;
  background: var(--bg-elevated);
  border: 1px solid var(--line);
  border-top: 2px solid var(--line);
}

.panel--review {
  border-top-color: var(--amber);
}

.panel--stuck {
  border-top-color: var(--bad);
}

.panel--feed {
  border-top-color: var(--cyan);
}

.panel__head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}

.panel__head h3 {
  margin: 0;
  color: var(--text);
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.14em;
}

.panel__count {
  color: var(--amber);
  font-size: 18px;
  font-weight: 700;
  line-height: 1;
}

.panel__count--bad {
  color: var(--bad);
}

.panel__count--muted {
  color: var(--muted);
}

.panel__more {
  margin-left: auto;
  color: var(--muted);
  font-size: 11px;
  text-decoration: none;
  white-space: nowrap;
}

.panel__more:hover {
  color: var(--cyan);
}

.rows {
  display: flex;
  flex-direction: column;
  margin-top: 6px;
}

.row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
  padding: 7px 0;
  border-bottom: 1px solid rgba(125, 137, 184, 0.12);
  font-size: 12px;
}

.row .code {
  color: var(--cyan);
  font-size: 11px;
}

.row .title {
  flex: 1 1 160px;
  min-width: 0;
  overflow: hidden;
  color: var(--text);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stamp {
  flex: 0 0 auto;
  font-size: 10px;
  letter-spacing: 0.04em;
}

.row--signal {
  align-items: flex-start;
}

.row--signal .title {
  white-space: normal;
  overflow-wrap: anywhere;
  line-height: 1.5;
}

.actions {
  display: flex;
  gap: 4px;
  margin-left: auto;
}

.mini {
  padding: 2px 7px 3px;
  background: transparent;
  border: 1px solid var(--line);
  color: var(--text);
  font-size: 11px;
  cursor: pointer;
}

.mini:hover:not(:disabled) {
  border-color: var(--cyan);
  color: var(--cyan);
}

.mini.danger:hover:not(:disabled) {
  border-color: var(--bad);
  color: var(--bad);
}

.mini:disabled {
  opacity: 0.4;
  cursor: default;
}

.dot {
  flex: 0 0 auto;
  width: 6px;
  height: 6px;
  margin-top: 6px;
  border-radius: 50%;
  background: var(--bad);
  box-shadow: 0 0 8px var(--bad);
}

.delivered {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: auto;
  padding-top: 10px;
  font-size: 11px;
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 7px;
  border: 1px solid rgba(48, 58, 84, 0.8);
  font-size: 11px;
}

.chip b {
  font-weight: 500;
}

.chip .ok {
  color: var(--ok);
}

.chip .bad {
  color: var(--bad);
}

.feed {
  display: flex;
  flex-direction: column;
  gap: 0;
  margin: 6px 0 0;
  padding: 0;
  list-style: none;
}

.feed__item {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px solid rgba(125, 137, 184, 0.12);
  font-size: 12px;
}

.feed__text {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.verdict {
  flex: 0 0 auto;
  padding: 1px 5px;
  border: 1px solid currentColor;
  font-size: 10px;
  letter-spacing: 0.06em;
}

.verdict--ok {
  color: var(--ok);
}

.verdict--muted {
  color: var(--muted);
}

.verdict--bad {
  color: var(--bad);
}

.mono {
  font-family: var(--mono);
  font-variant-numeric: tabular-nums;
}

.loading {
  margin-top: 12px;
  font-size: 12px;
}
</style>
