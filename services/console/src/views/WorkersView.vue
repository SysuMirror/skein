<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { fetchSessions, fetchSessionTurns, type SessionRecord, type TurnRecord } from "../api";
import EmptyState from "../components/EmptyState.vue";
import ErrorBar from "../components/ErrorBar.vue";
import StatusTag from "../components/StatusTag.vue";
import WorkerTranscript from "../components/WorkerTranscript.vue";

/** 有轮次且 updated_at 落在这个窗口内时用 info 色。不是接口里的状态字段。 */
const RECENT_UPDATE_S = 600;
const POLL_MS = 10000;

interface SessionStatus {
  status: string;
  label: string;
  title: string;
}

const route = useRoute();
const router = useRouter();
const sessions = ref<SessionRecord[]>([]);
const loaded = ref(false);
const loading = ref(true);
const error = ref("");
const nowSec = ref(Date.now() / 1000);
const selectedId = ref<string | null>(null);
const opened = ref<SessionRecord | null>(null);
const turns = ref<TurnRecord[] | null>(null);
const turnsFor = ref<string | null>(null);
const turnsLoading = ref(false);
const turnsError = ref("");

let timer: number | undefined;
let disposed = false;
let listSeq = 0;
let turnSeq = 0;

function formatUnix(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return "—";
  const date = new Date(seconds * 1000);
  if (Number.isNaN(date.getTime())) return "—";
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function formatAge(age: number): string {
  if (!Number.isFinite(age)) return "时间未知";
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

function sessionStatus(session: SessionRecord, now: number): SessionStatus {
  const turnCount = Number.isFinite(session.turn_count) ? session.turn_count : 0;
  const updated =
    typeof session.updated_at === "number" && Number.isFinite(session.updated_at)
      ? session.updated_at
      : null;
  const title = `由 turn_count=${turnCount} 与 updated_at（${formatUnix(updated)}）推断，不是接口字段`;
  if (turnCount <= 0) return { status: "neutral", label: "无轮次", title };
  if (updated == null || updated <= 0) return { status: "neutral", label: "更新时间未知", title };
  const age = now - updated;
  const ageText = formatAge(age);
  const label =
    ageText === "刚刚" ? "刚刚更新" : ageText === "时钟超前" ? "更新时间超前" : `${ageText}更新`;
  return {
    status: age <= RECENT_UPDATE_S ? "info" : "neutral",
    label,
    title,
  };
}

function turnCountText(session: SessionRecord): string {
  if (!Number.isFinite(session.turn_count)) return "—";
  return `${session.turn_count} 轮`;
}

function pinnedText(session: SessionRecord): string {
  return Number(session.pinned) !== 0 ? "钉住" : "未钉住";
}

const rows = computed(() =>
  sessions.value.map((session) => ({
    session,
    status: sessionStatus(session, nowSec.value),
    turns: turnCountText(session),
    pinned: pinnedText(session),
    pinnedOn: Number(session.pinned) !== 0,
    lastTurn: formatUnix(session.last_turn_at),
  })),
);

const countText = computed(() => (loaded.value ? String(sessions.value.length) : "—"));
const selectedInList = computed(() =>
  selectedId.value != null && sessions.value.some((session) => session.id === selectedId.value),
);
const transcriptTurns = computed(() =>
  turnsFor.value === selectedId.value ? turns.value : null,
);

function errorMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

function rememberSelection(list: SessionRecord[]) {
  if (!selectedId.value) return;
  const found = list.find((session) => session.id === selectedId.value);
  if (found) opened.value = found;
}

async function loadList() {
  const seq = ++listSeq;
  if (!loaded.value) loading.value = true;
  try {
    const body = await fetchSessions();
    if (disposed || seq !== listSeq) return;
    if (!body || !Array.isArray(body.sessions)) {
      throw new Error("会话响应缺少 sessions");
    }
    nowSec.value = Date.now() / 1000;
    sessions.value = body.sessions;
    rememberSelection(body.sessions);
    error.value = "";
    loaded.value = true;
  } catch (err) {
    if (disposed || seq !== listSeq) return;
    nowSec.value = Date.now() / 1000;
    error.value = errorMessage(err, "加载会话失败");
  } finally {
    if (!disposed && seq === listSeq) loading.value = false;
  }
}

async function loadTurns(id: string) {
  const seq = ++turnSeq;
  turnsLoading.value = true;
  if (turnsFor.value !== id) {
    turns.value = null;
    turnsError.value = "";
  }
  try {
    const body = await fetchSessionTurns(id, true);
    if (disposed || seq !== turnSeq || selectedId.value !== id) return;
    if (!body || !Array.isArray(body.turns)) {
      throw new Error("轮次响应缺少 turns");
    }
    turns.value = body.turns;
    turnsFor.value = id;
    turnsError.value = "";
    if (body.session && body.session.id === id) opened.value = body.session;
  } catch (err) {
    if (disposed || seq !== turnSeq || selectedId.value !== id) return;
    turnsError.value = errorMessage(err, "读取轮次失败");
    if (turnsFor.value !== id) {
      turns.value = null;
      turnsFor.value = null;
    }
  } finally {
    if (!disposed && seq === turnSeq) turnsLoading.value = false;
  }
}

function selectSession(session: SessionRecord) {
  selectedId.value = session.id;
  opened.value = session;
  if (route.params.id !== session.id) {
    void router.push(`/workers/${encodeURIComponent(session.id)}`);
  }
  void loadTurns(session.id);
}

function syncWorkerRoute(): void {
  const raw = route.params.id;
  const id = typeof raw === "string" ? raw : "";
  if (!id) {
    selectedId.value = null;
    opened.value = null;
    return;
  }
  if (selectedId.value !== id) {
    selectedId.value = id;
    void loadTurns(id);
  }
}

watch(() => route.params.id, syncWorkerRoute, { immediate: true });

async function refresh() {
  await loadList();
  if (selectedId.value) await loadTurns(selectedId.value);
}

function retryTurns() {
  if (selectedId.value) void loadTurns(selectedId.value);
}

onMounted(() => {
  void loadList();
  timer = window.setInterval(() => {
    void loadList();
  }, POLL_MS);
});

onBeforeUnmount(() => {
  disposed = true;
  if (timer !== undefined) window.clearInterval(timer);
});
</script>

<template>
  <section class="page" :class="{ 'page--error': error }" aria-labelledby="workers-heading">
    <header class="page-head">
      <h2 id="workers-heading" class="page__title">Worker 会话</h2>
      <div class="page-head__actions">
        <p class="count" title="当前列表中的会话总数">
          <span class="muted">总数</span>
          <span class="mono">{{ countText }}</span>
        </p>
        <button type="button" class="btn" @click="refresh">刷新</button>
      </div>
    </header>

    <ErrorBar v-if="error" :message="error" @retry="refresh" />

    <p v-if="loading && !loaded" class="sr">正在加载会话</p>
    <div v-if="loading && !loaded" class="split" aria-busy="true">
      <div class="pane">
        <div v-for="index in 4" :key="index" class="skeleton" aria-hidden="true">
          <span></span>
          <span></span>
          <span></span>
        </div>
      </div>
      <div class="pane">
        <div class="skeleton skeleton--tall" aria-hidden="true">
          <span></span>
          <span></span>
          <span></span>
        </div>
      </div>
    </div>
    <EmptyState
      v-else-if="loaded && sessions.length === 0"
      title="暂无会话"
      description="派工后节点会创建 Claude Code 会话，并同步轮次到这里。"
    />
    <div v-else-if="sessions.length" class="split">
      <div class="pane" role="list" aria-label="会话列表">
        <button
          v-for="row in rows"
          :key="row.session.id"
          type="button"
          class="session"
          role="listitem"
          :class="{ 'is-selected': row.session.id === selectedId, 'is-pinned': row.pinnedOn }"
          :aria-pressed="row.session.id === selectedId"
          @click="selectSession(row.session)"
        >
          <span class="session__title">
            {{ row.session.ticket_id ? `工单 #${row.session.ticket_id}` : "未挂工单" }}
          </span>
          <span class="session__id mono" :title="row.session.id">
            会话 {{ row.session.id.slice(0, 8) }}
          </span>
          <span class="session__line">
            <span class="muted">节点</span>
            <span>{{ row.session.node_id || "—" }}</span>
          </span>
          <span class="session__facts">
            <span :class="{ 'is-pinned': row.pinnedOn }">{{ row.pinned }}</span>
            <span class="mono">{{ row.turns }}</span>
            <span>最近轮次 {{ row.lastTurn }}</span>
          </span>
          <span class="session__status" :title="row.status.title">
            <StatusTag :status="row.status.status" :label="row.status.label" />
          </span>
        </button>
      </div>
      <div class="pane pane--transcript">
        <EmptyState
          v-if="!selectedId || !opened"
          title="未选择会话"
          description="从左侧选择一条会话读取轮次。"
        />
        <WorkerTranscript
          v-else
          :session-id="opened.id"
          :node-id="opened.node_id"
          :ticket-id="opened.ticket_id"
          :absent="loaded && !selectedInList"
          :loading="turnsLoading"
          :error="turnsError"
          :turns="transcriptTurns"
          @retry="retryTurns"
        />
      </div>
    </div>
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

.btn:focus-visible,
.session:focus-visible {
  outline: 1px solid var(--cyan);
  outline-offset: 2px;
}

.split {
  display: grid;
  grid-template-columns: minmax(240px, 340px) minmax(0, 1fr);
  gap: 8px 22px;
  align-items: stretch;
  min-width: 0;
  max-height: calc(100vh - 156px);
}

.page--error .split {
  max-height: calc(100vh - 204px);
}

.pane {
  min-width: 0;
  min-height: 0;
  max-height: calc(100vh - 156px);
  padding-left: 4px;
  overflow: auto;
}

.page--error .pane {
  max-height: calc(100vh - 204px);
}

.pane--transcript {
  padding: 2px 4px 8px 16px;
  border: none;
  background: transparent;
  box-shadow: inset 1px 0 0 rgba(79, 216, 235, 0.28);
}

.skeleton {
  min-height: 78px;
  margin: 0;
  padding: 10px 8px 8px 14px;
  border: none;
  background: transparent;
  box-shadow: inset 2px 0 0 rgba(79, 216, 235, 0.22);
}

.skeleton + .skeleton {
  box-shadow:
    inset 2px 0 0 rgba(79, 216, 235, 0.22),
    inset 0 1px 0 rgba(125, 137, 184, 0.12);
}

.skeleton span {
  display: block;
  width: 92%;
  height: 1px;
  margin: 12px 0;
  background: linear-gradient(90deg, rgba(125, 137, 184, 0.5), transparent 82%);
}

.skeleton span:first-child {
  width: 64%;
  height: 2px;
  background: linear-gradient(90deg, rgba(79, 216, 235, 0.5), transparent 80%);
}

.skeleton--tall {
  min-height: 220px;
  box-shadow: inset 1px 0 0 rgba(79, 216, 235, 0.28);
}

.session {
  position: relative;
  display: block;
  width: 100%;
  min-width: 0;
  max-width: 100%;
  margin: 0;
  padding: 8px 8px 8px 14px;
  text-align: left;
  font: inherit;
  color: inherit;
  background: transparent;
  border: none;
  cursor: pointer;
}

.session + .session {
  box-shadow: inset 0 1px 0 rgba(125, 137, 184, 0.12);
}

.session::before {
  content: "";
  position: absolute;
  top: 8px;
  bottom: 8px;
  left: 0;
  width: 2px;
  background: rgba(79, 216, 235, 0.35);
  box-shadow: 0 0 6px rgba(79, 216, 235, 0.28);
}

.session.is-pinned::before {
  background: var(--warn);
  box-shadow: 0 0 8px var(--warn);
}

.session:hover,
.session.is-selected {
  background: linear-gradient(90deg, rgba(79, 216, 235, 0.08), transparent 74%);
}

.session__title {
  display: block;
  min-width: 0;
  overflow: hidden;
  color: var(--text);
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session__id {
  display: block;
  min-width: 0;
  overflow: hidden;
  color: var(--muted);
  font-size: 11px;
  font-weight: 400;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session.is-selected .session__id,
.session:hover .session__id {
  color: #fff;
  text-shadow: 0 0 8px rgba(79, 216, 235, 0.45);
}

.session__line,
.session__facts {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 10px;
  min-width: 0;
  margin-top: 3px;
}

.session__line span,
.session__facts span {
  min-width: 0;
  max-width: 100%;
  overflow-wrap: anywhere;
}

.session__facts {
  color: var(--muted);
  font-size: 12px;
}

.session__facts .mono {
  font-size: 11px;
}

.session__status {
  display: inline-flex;
  max-width: 100%;
  margin-top: 4px;
}

.is-pinned .session__facts > span:first-child,
.session__facts .is-pinned {
  color: var(--warn);
  text-shadow: 0 0 8px rgba(245, 185, 66, 0.4);
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

@container (max-width: 760px) {
  .split,
  .page--error .split {
    grid-template-columns: minmax(0, 1fr);
    max-height: none;
  }

  .pane,
  .page--error .pane {
    max-height: 320px;
  }
}
</style>
