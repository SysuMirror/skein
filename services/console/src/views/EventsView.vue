<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import {
  fetchEvents,
  fetchThread,
  type ControlEvent,
  type ThreadDetailResponse,
  type Ticket,
} from "../api";
import EmptyState from "../components/EmptyState.vue";
import ErrorBar from "../components/ErrorBar.vue";
import StatusTag from "../components/StatusTag.vue";

const POLL_MS = 5000;
const EVENT_LIMIT = 200;
const ORIGINS = ["forum", "rss", "chat", "other"] as const;
const VERDICT_FILTERS = [
  { value: "", label: "全部" },
  { value: "ignore", label: "忽略" },
  { value: "record", label: "记账" },
  { value: "process", label: "处理" },
] as const;

type VerdictFilter = (typeof VERDICT_FILTERS)[number]["value"];

const router = useRouter();
const events = ref<ControlEvent[]>([]);
const verdict = ref<VerdictFilter>("");
const origin = ref("");
const error = ref<string | null>(null);
const loading = ref(false);
const refreshing = ref(false);
const refreshedAt = ref<number | null>(null);
const drawerOpen = ref(false);
const activeEventId = ref<string | null>(null);
const threadLoading = ref(false);
const threadError = ref<string | null>(null);
const detail = ref<ThreadDetailResponse | null>(null);

let alive = true;
let seq = 0;
let threadSeq = 0;
let timer = 0;
let shownFilter = "|";

const rows = computed(() =>
  events.value.map((event) => ({
    event,
    payload: payloadText(event.payload),
    verdict: verdictTag(event.verdict),
  })),
);

const refreshLabel = computed(() =>
  refreshedAt.value == null ? "—" : formatStamp(refreshedAt.value),
);

const refreshIso = computed(() =>
  refreshedAt.value == null ? undefined : new Date(refreshedAt.value).toISOString(),
);

const activeEvent = computed(
  () => events.value.find((event) => event.id === activeEventId.value) ?? null,
);

const threadId = computed(
  () => detail.value?.thread.id ?? activeEvent.value?.thread_id ?? "",
);

const emptyDescription = computed(() =>
  verdict.value || origin.value
    ? "当前筛选没有命中。换一个判定或来源后再看。"
    : "控制面当前没有事件。",
);

function verdictTag(value: string): { status: string; label: string } {
  switch (value) {
    case "ignore":
      return { status: "ignore", label: "忽略" };
    case "record":
      return { status: "info", label: "记账" };
    case "process":
      return { status: "ok", label: "处理" };
    default:
      return { status: value || "neutral", label: value || "—" };
  }
}

function statusLabel(status: string): string {
  switch (status) {
    case "staged":
      return "待派";
    case "assigned":
      return "已派";
    case "review":
      return "待验收";
    case "done":
    case "succeeded":
      return "完成";
    case "success":
      return "成功";
    case "rejected":
      return "驳回";
    case "running":
      return "进行中";
    case "failed":
    case "error":
      return "失败";
    case "pending":
      return "待处理";
    case "idle":
      return "空闲";
    case "busy":
      return "忙碌";
    case "creating":
      return "创建中";
    case "cancelled":
    case "canceled":
      return "已取消";
    default:
      return status || "—";
  }
}

function tagStatus(status: string): string {
  if (status === "assigned") return "running";
  return status || "neutral";
}

function formatStamp(ts: number): string {
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return "—";
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function formatUnix(ts: number | null | undefined): string {
  if (ts == null || !Number.isFinite(ts)) return "—";
  const ms = ts > 1e12 ? ts : ts * 1000;
  return formatStamp(ms);
}

function serialize(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "";
  if (typeof value === "boolean") return String(value);
  if (typeof value === "bigint") return value.toString();
  if (value == null) return "";
  if (typeof value !== "object") return "";
  const seen = new WeakSet<object>();
  try {
    return (
      JSON.stringify(value, (key, current: unknown) => {
        if (key === "__proto__" || key === "constructor" || key === "prototype") return undefined;
        if (typeof current === "bigint") return current.toString();
        if (typeof current === "function" || typeof current === "symbol") return undefined;
        if (current && typeof current === "object") {
          if (seen.has(current)) return "[Circular]";
          seen.add(current);
        }
        return current;
      }) ?? ""
    );
  } catch {
    return "";
  }
}

function payloadRecord(payload: unknown): Record<string, unknown> | null {
  let value: unknown = payload;
  if (typeof value === "string") {
    const text = value.trim();
    if (!text.startsWith("{")) return null;
    try {
      value = JSON.parse(text) as unknown;
    } catch {
      return null;
    }
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function fieldText(record: Record<string, unknown>, key: string): string {
  const value = record[key];
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return String(value);
  return "";
}

/** 摘要优先给人读的字段。没有 title/text/content 时才退回截断后的 JSON。 */
function payloadText(payload: unknown): { short: string; full: string; json: boolean } {
  const record = payloadRecord(payload);
  const preferred = record
    ? fieldText(record, "title") || fieldText(record, "text") || fieldText(record, "content")
    : "";
  const raw = preferred || serialize(payload);
  const full = raw.replace(/\s+/g, " ").trim();
  const json = !preferred && full.length > 0 && (record !== null || (typeof payload === "object" && payload !== null));
  if (!full) return { short: "—", full: "", json: false };
  const chars = Array.from(full);
  if (chars.length <= 120) return { short: full, full, json };
  return {
    short: `${chars.slice(0, 119).join("")}…`,
    full: chars.slice(0, 2000).join(""),
    json,
  };
}

function errorMessage(err: unknown): string {
  if (err instanceof Error && err.message) return err.message;
  return "网络错误";
}

function setVerdict(value: VerdictFilter) {
  verdict.value = value;
}

async function load(fromUser = false) {
  const token = ++seq;
  const filterKey = `${verdict.value}|${origin.value.trim()}`;
  if (shownFilter !== filterKey) {
    events.value = [];
    error.value = null;
    loading.value = true;
  }
  const initial = events.value.length === 0 && error.value == null;
  if (initial) loading.value = true;
  if (fromUser || initial) refreshing.value = true;
  try {
    const result = await fetchEvents({
      limit: EVENT_LIMIT,
      verdict: verdict.value || undefined,
      origin: origin.value.trim() || undefined,
    });
    if (!alive || token !== seq) return;
    events.value = Array.isArray(result.events) ? result.events : [];
    shownFilter = filterKey;
    error.value = null;
    refreshedAt.value = Date.now();
  } catch (err) {
    if (!alive || token !== seq) return;
    if (shownFilter !== filterKey) events.value = [];
    error.value = errorMessage(err);
  } finally {
    if (alive && token === seq) {
      loading.value = false;
      refreshing.value = false;
    }
  }
}

async function openRow(event: ControlEvent) {
  const token = ++threadSeq;
  activeEventId.value = event.id;
  drawerOpen.value = true;
  detail.value = null;
  threadError.value = null;
  if (!event.thread_id) {
    threadLoading.value = false;
    threadError.value = "该事件没有关联 Thread";
    return;
  }
  threadLoading.value = true;
  try {
    const next = await fetchThread(event.thread_id);
    if (!alive || token !== threadSeq) return;
    detail.value = next;
  } catch (err) {
    if (!alive || token !== threadSeq) return;
    threadError.value = errorMessage(err);
  } finally {
    if (alive && token === threadSeq) threadLoading.value = false;
  }
}

function reloadThread() {
  const event = activeEvent.value;
  if (event) void openRow(event);
}

function closeDrawer() {
  drawerOpen.value = false;
}

function openTickets() {
  void router.push("/tickets");
}

function ticketLabel(ticket: Ticket): string {
  return `打开工单列表，${ticket.code || ticket.id}`;
}

function onKeydown(ev: KeyboardEvent) {
  if (ev.key === "Escape" && drawerOpen.value) closeDrawer();
}

watch([verdict, origin], () => {
  void load();
});

onMounted(() => {
  void load();
  timer = window.setInterval(() => {
    void load();
  }, POLL_MS);
  window.addEventListener("keydown", onKeydown);
});

onUnmounted(() => {
  alive = false;
  window.clearInterval(timer);
  window.removeEventListener("keydown", onKeydown);
});
</script>

<template>
  <section class="page">
    <header class="head">
      <h2 class="page__title">事件</h2>
      <div class="head__meta">
        <span class="mono" :title="`最多显示 ${EVENT_LIMIT} 条`">{{ events.length }} 条</span>
        <span>
          最近刷新
          <time class="mono" :datetime="refreshIso">{{ refreshLabel }}</time>
        </span>
        <button type="button" class="btn" :disabled="refreshing" @click="load(true)">
          {{ refreshing ? "刷新中" : "刷新" }}
        </button>
      </div>
    </header>

    <ErrorBar v-if="error" :message="error" @retry="load(true)" />

    <div class="filters">
      <div class="filters__verdicts" role="group" aria-label="准入判定">
        <button
          v-for="item in VERDICT_FILTERS"
          :key="item.label"
          type="button"
          class="btn"
          :aria-pressed="verdict === item.value"
          :title="item.value || undefined"
          @click="setVerdict(item.value)"
        >
          {{ item.label }}
        </button>
      </div>
      <label class="origin">
        <span>来源</span>
        <select v-model="origin" aria-label="来源">
          <option value="">全部</option>
          <option v-for="item in ORIGINS" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
    </div>

    <p v-if="loading && events.length === 0" class="muted load">加载中</p>
    <EmptyState
      v-else-if="!loading && !error && events.length === 0"
      title="暂无事件"
      :description="emptyDescription"
    />

    <div v-if="events.length > 0" class="table-wrap">
      <table class="grid">
        <colgroup>
          <col class="col-time" />
          <col class="col-origin" />
          <col />
          <col />
          <col class="col-verdict" />
          <col />
        </colgroup>
        <thead>
          <tr>
            <th>时间</th>
            <th>来源</th>
            <th>来源 id</th>
            <th>Thread</th>
            <th>准入判定</th>
            <th>摘要</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="row.event.id"
            :class="{ 'is-active': row.event.id === activeEventId && drawerOpen }"
            tabindex="0"
            @click="openRow(row.event)"
            @keydown.enter.prevent="openRow(row.event)"
          >
            <td class="mono" :title="formatUnix(row.event.created_at)">
              {{ formatUnix(row.event.created_at) }}
            </td>
            <td :title="row.event.origin">{{ row.event.origin || "—" }}</td>
            <td class="mono" :title="row.event.origin_id">{{ row.event.origin_id || "—" }}</td>
            <td class="mono" :title="row.event.thread_id ?? ''">{{ row.event.thread_id || "—" }}</td>
            <td>
              <StatusTag
                :class="{ 'is-code': row.verdict.status === row.verdict.label }"
                :status="row.verdict.status"
                :label="row.verdict.label"
                :title="row.event.verdict"
              />
            </td>
            <td :class="{ 'is-json': row.payload.json }" :title="row.payload.full">{{ row.payload.short }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <Teleport to="body">
      <aside v-if="drawerOpen" class="drawer" aria-label="Thread">
        <header class="drawer__head">
          <div class="drawer__heading">
            <h3>Thread</h3>
            <p class="mono drawer__id">{{ threadId || "无" }}</p>
          </div>
          <button type="button" class="btn" @click="closeDrawer">关闭</button>
        </header>

        <p v-if="detail" class="drawer__meta muted">
          <span>{{ detail.thread.origin || "—" }}</span>
          <span class="mono">{{ detail.thread.origin_id || "—" }}</span>
          <span class="mono">{{ formatUnix(detail.thread.created_at) }}</span>
        </p>

        <p v-if="threadLoading" class="muted load">加载中</p>
        <div v-else-if="threadError" class="drawer__error" role="alert">
          <span>{{ threadError }}</span>
          <button v-if="activeEvent?.thread_id" type="button" class="btn" @click="reloadThread">
            重试
          </button>
        </div>

        <template v-if="detail">
          <section class="block">
            <h4>事件 {{ detail.events.length }}</h4>
            <p v-if="detail.events.length === 0" class="muted">暂无事件</p>
            <ul v-else class="plain">
              <li v-for="item in detail.events" :key="item.id">
                <span class="mono">{{ formatUnix(item.created_at) }}</span>
                <StatusTag
                  :class="{ 'is-code': verdictTag(item.verdict).status === verdictTag(item.verdict).label }"
                  :status="verdictTag(item.verdict).status"
                  :label="verdictTag(item.verdict).label"
                  :title="item.verdict"
                />
                <span
                  class="clip"
                  :class="{ 'is-json': payloadText(item.payload).json }"
                  :title="payloadText(item.payload).full"
                >
                  {{ payloadText(item.payload).short }}
                </span>
              </li>
            </ul>
          </section>

          <section class="block">
            <h4>工单 {{ detail.tickets.length }}</h4>
            <p v-if="detail.tickets.length === 0" class="muted">暂无工单</p>
            <div v-else class="tickets">
              <button
                v-for="ticket in detail.tickets"
                :key="ticket.id"
                type="button"
                class="ticket"
                :aria-label="ticketLabel(ticket)"
                @click="openTickets"
              >
                <span class="mono ticket__code">{{ ticket.code || ticket.id }}</span>
                <span class="clip ticket__title" :title="ticket.title">{{ ticket.title || "—" }}</span>
                <StatusTag
                  :class="{ 'is-code': tagStatus(ticket.status) === statusLabel(ticket.status) }"
                  :status="tagStatus(ticket.status)"
                  :label="statusLabel(ticket.status)"
                  :title="ticket.status"
                />
              </button>
            </div>
          </section>
        </template>
      </aside>
    </Teleport>
  </section>
</template>

<style scoped>
.page {
  min-width: 0;
  max-width: 100%;
  overflow-x: clip;
}

.head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px 16px;
  min-width: 0;
  margin-bottom: 8px;
  padding-bottom: 8px;
  background-image: linear-gradient(90deg, rgba(79, 216, 235, 0.4), transparent 72%);
  background-repeat: no-repeat;
  background-position: left bottom;
  background-size: 100% 1px;
}

.head .page__title {
  margin: 0;
  color: var(--muted);
  font-weight: 500;
  letter-spacing: 0.22em;
}

.head__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px 12px;
  min-width: 0;
  margin-left: auto;
  color: var(--muted);
}

.head__meta .mono {
  color: var(--text);
  font-size: 12px;
}

.filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 14px;
  min-width: 0;
  margin-bottom: 8px;
}

.filters__verdicts {
  display: flex;
  flex-wrap: wrap;
  gap: 2px;
  min-width: 0;
}

.btn {
  position: relative;
  padding: 4px 8px 7px;
  background: transparent;
  border: none;
  color: var(--muted);
  letter-spacing: 0.08em;
  cursor: pointer;
}

.btn::after {
  content: "";
  position: absolute;
  right: 12%;
  bottom: 2px;
  left: 12%;
  height: 1px;
  background: linear-gradient(90deg, transparent, currentColor, transparent);
  opacity: 0.85;
  transform: scaleX(0);
  transition: transform 0.22s ease;
}

.btn:hover:not(:disabled),
.btn:focus-visible {
  color: var(--cyan);
  text-shadow: 0 0 8px rgba(79, 216, 235, 0.7);
}

.btn:hover:not(:disabled)::after,
.btn:focus-visible::after,
.btn[aria-pressed="true"]::after {
  transform: scaleX(1);
}

.btn[aria-pressed="true"] {
  color: var(--cyan);
  text-shadow: 0 0 8px rgba(79, 216, 235, 0.75);
}

.btn:disabled {
  cursor: default;
  color: var(--muted);
  text-shadow: none;
}

.btn:focus-visible {
  outline: 1px solid var(--cyan);
  outline-offset: 2px;
}

.origin {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: var(--muted);
  letter-spacing: 0.08em;
}

.origin select {
  max-width: 100%;
  padding: 4px 2px;
  background: transparent;
  color: var(--text);
  border: none;
  border-bottom: 1px solid rgba(125, 137, 184, 0.35);
}

.origin select:focus {
  outline: none;
  border-bottom-color: var(--cyan);
  box-shadow: 0 6px 12px -8px rgba(79, 216, 235, 0.6);
}

.load {
  margin: 8px 0;
  letter-spacing: 0.12em;
  text-align: center;
}

.table-wrap {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  max-height: calc(100vh - 180px);
  overflow: auto;
  border: none;
}

.grid {
  width: 100%;
  max-width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: 12px;
  box-shadow: inset 0 1px 0 rgba(79, 216, 235, 0.28);
}

.col-time {
  width: 168px;
}

.col-origin {
  width: 72px;
}

.col-verdict {
  width: 96px;
}

.grid th,
.grid td {
  padding: 6px 8px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: left;
  font-weight: 400;
  border: none;
  box-shadow: inset 0 -1px 0 rgba(79, 216, 235, 0.12);
}

.grid th {
  position: sticky;
  top: 0;
  z-index: 1;
  color: var(--muted);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.12em;
  background: rgba(4, 6, 14, 0.94);
}

.grid tbody tr {
  cursor: pointer;
}

.grid tbody tr:hover td {
  background: linear-gradient(90deg, rgba(79, 216, 235, 0.07), transparent 72%);
}

.grid tbody tr.is-active td {
  background: linear-gradient(90deg, rgba(79, 216, 235, 0.1), transparent 76%);
}

.grid tbody tr.is-active td:first-child {
  box-shadow: inset 2px 0 0 var(--cyan), inset 0 -1px 0 rgba(79, 216, 235, 0.2);
}

.grid tbody tr:focus-visible {
  outline: 1px solid var(--cyan);
  outline-offset: -1px;
}

.grid :deep(.status-tag),
.plain :deep(.status-tag),
.ticket :deep(.status-tag) {
  text-shadow: 0 0 8px color-mix(in srgb, currentColor 55%, transparent);
}

.grid :deep(.status-tag.is-code) {
  font-family: var(--mono);
  font-size: 11px;
}

td.is-json,
.clip.is-json {
  color: var(--muted);
  font-family: var(--mono);
  font-size: 11px;
}

.drawer {
  position: fixed;
  top: 48px;
  right: 0;
  bottom: 0;
  z-index: 30;
  display: flex;
  flex-direction: column;
  width: min(420px, 100%);
  max-width: 100%;
  min-width: 0;
  overflow: auto;
  overflow-x: clip;
  overscroll-behavior: contain;
  box-sizing: border-box;
  padding: 18px 18px 28px;
  color: var(--text);
  border: none;
  background: linear-gradient(270deg, rgba(10, 16, 36, 0.97), rgba(6, 9, 20, 0.94));
  box-shadow: -1px 0 0 rgba(79, 216, 235, 0.35), -24px 0 48px rgba(0, 0, 0, 0.45);
}

.drawer__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
}

.drawer__heading {
  min-width: 0;
}

.drawer__heading h3 {
  margin: 0;
  color: #fff;
  font-size: 14px;
  font-weight: 650;
  letter-spacing: 0.16em;
  text-shadow: 0 0 12px rgba(79, 216, 235, 0.4);
}

.drawer__id {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 11px;
  overflow-wrap: anywhere;
}

.drawer__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
  min-width: 0;
  margin: 8px 0 0;
  font-family: var(--mono);
  font-size: 11px;
}

.drawer__error {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  padding: 6px 8px 6px 12px;
  background: transparent;
  border: none;
  box-shadow: inset 2px 0 0 var(--bad);
  color: var(--bad);
}

.block {
  min-width: 0;
  margin-top: 16px;
  padding-left: 12px;
  box-shadow: inset 1px 0 0 rgba(79, 216, 235, 0.28);
}

.block h4 {
  margin: 0 0 6px;
  color: var(--cyan);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.16em;
  text-shadow: 0 0 8px rgba(79, 216, 235, 0.35);
}

.plain {
  list-style: none;
  margin: 0;
  padding: 0;
  min-width: 0;
  max-height: 240px;
  overflow: auto;
  border: none;
}

.plain li {
  display: grid;
  grid-template-columns: minmax(0, 148px) auto minmax(0, 1fr);
  gap: 8px;
  align-items: baseline;
  min-width: 0;
  padding: 6px 2px;
  overflow: hidden;
  border: none;
  box-shadow: inset 0 -1px 0 rgba(125, 137, 184, 0.12);
  font-size: 12px;
}

.plain li > .mono {
  color: var(--muted);
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.plain :deep(.status-tag.is-code),
.ticket :deep(.status-tag.is-code) {
  font-family: var(--mono);
  font-size: 11px;
}

.clip {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tickets {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.ticket {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  max-width: 100%;
  min-width: 0;
  padding: 6px 4px 6px 10px;
  background: transparent;
  border: none;
  box-shadow: inset 2px 0 0 rgba(79, 216, 235, 0.35);
  color: inherit;
  text-align: left;
  font: inherit;
  cursor: pointer;
}

.ticket + .ticket {
  box-shadow:
    inset 2px 0 0 rgba(79, 216, 235, 0.35),
    inset 0 1px 0 rgba(125, 137, 184, 0.1);
}

.ticket:hover {
  background: linear-gradient(90deg, rgba(79, 216, 235, 0.08), transparent 74%);
}

.ticket:focus-visible {
  outline: 1px solid var(--cyan);
  outline-offset: 1px;
}

.ticket__code {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--cyan);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ticket__title {
  flex: 1 1 auto;
}
</style>
