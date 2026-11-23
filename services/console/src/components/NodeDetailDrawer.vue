<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import {
  fetchSessions,
  fetchTopology,
  type Dispatch,
  type NodeRecord,
  type SessionRecord,
} from "../api";
import ErrorBar from "./ErrorBar.vue";
import StatusTag from "./StatusTag.vue";

const props = defineProps<{ node: NodeRecord }>();
const emit = defineEmits<{ (event: "close"): void }>();

const sessions = ref<SessionRecord[]>([]);
const dispatches = ref<Dispatch[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
let alive = true;

function fmt(ts: number | null | undefined): string {
  if (!ts || !Number.isFinite(ts)) return "—";
  const date = new Date(ts * 1000);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}`;
}

function platforms(raw: string): string {
  try {
    const value: unknown = JSON.parse(raw);
    if (Array.isArray(value)) return value.map(String).join(" · ") || "—";
  } catch {
    // 原始字符串直接展示
  }
  return raw || "—";
}

async function load() {
  loading.value = true;
  error.value = null;
  try {
    const [sessionBody, topology] = await Promise.all([
      fetchSessions({ node_id: props.node.id, limit: 100 }),
      fetchTopology(),
    ]);
    if (!alive) return;
    sessions.value = sessionBody.sessions;
    dispatches.value = topology.dispatches
      .filter((item) => item.node_id === props.node.id)
      .slice(0, 30);
  } catch (err) {
    if (alive) error.value = err instanceof Error ? err.message : "节点详情加载失败";
  } finally {
    loading.value = false;
  }
}

function onKey(event: KeyboardEvent) {
  if (event.key === "Escape") emit("close");
}

watch(() => props.node.id, load, { immediate: true });

onMounted(() => window.addEventListener("keydown", onKey));
onBeforeUnmount(() => {
  alive = false;
  window.removeEventListener("keydown", onKey);
});
</script>

<template>
  <div class="mask" @click="emit('close')"></div>
  <aside class="drawer" role="dialog" aria-modal="true" aria-label="节点详情">
    <header class="drawer__head">
      <div>
        <h3>{{ node.name || node.id }}</h3>
        <p class="mono muted">{{ node.id }}</p>
      </div>
      <button type="button" class="drawer__close" aria-label="关闭" @click="emit('close')">
        ×
      </button>
    </header>

    <error-bar v-if="error" :message="error" @retry="load" />
    <p v-if="loading" class="muted pad">加载中…</p>

    <section class="block">
      <h4>节点</h4>
      <dl class="kv">
        <dt>状态</dt>
        <dd><status-tag :status="node.status" :label="node.status" /></dd>
        <dt>主机</dt>
        <dd class="mono">{{ node.host || "—" }}</dd>
        <dt>平台</dt>
        <dd>{{ platforms(node.platforms) }}</dd>
        <dt>容量</dt>
        <dd class="mono">{{ node.max_workers }}</dd>
        <dt>最近心跳</dt>
        <dd class="mono">{{ fmt(node.last_seen) }}</dd>
      </dl>
    </section>

    <section class="block">
      <h4>会话 <span class="muted">{{ sessions.length }}</span></h4>
      <div v-if="sessions.length" class="rows">
        <div v-for="session in sessions" :key="session.id" class="row">
          <span class="mono">{{ session.id }}</span>
          <span class="muted">工单 {{ session.ticket_id || "—" }}</span>
          <span class="muted">{{ session.pinned ? "钉住" : "未钉住" }}</span>
          <span class="muted">{{ session.turn_count }} 轮</span>
          <span class="muted mono">{{ fmt(session.last_turn_at) }}</span>
        </div>
      </div>
      <p v-else class="muted">该节点暂无会话</p>
    </section>

    <section class="block">
      <h4>派工 <span class="muted">{{ dispatches.length }}</span></h4>
      <div v-if="dispatches.length" class="rows">
        <div v-for="item in dispatches" :key="item.id" class="row">
          <span class="mono">{{ item.ticket_id }}</span>
          <span class="mono muted">{{ item.worker_id || "—" }}</span>
          <span class="state" :class="`state--${item.state}`">{{ item.state }}</span>
          <span class="muted mono">{{ fmt(item.updated_at) }}</span>
        </div>
      </div>
      <p v-else class="muted">该节点暂无派工记录</p>
    </section>
  </aside>
</template>

<style scoped>
.mask {
  position: fixed;
  inset: 0;
  z-index: 90;
  background: rgba(2, 3, 10, 0.55);
  backdrop-filter: blur(3px);
}

.drawer {
  position: fixed;
  top: 0;
  right: 0;
  z-index: 100;
  width: 560px;
  max-width: 96vw;
  height: 100%;
  overflow-y: auto;
  padding: 20px 22px 40px;
  background: linear-gradient(270deg, rgba(10, 16, 36, 0.98), rgba(6, 9, 20, 0.96));
  box-shadow: -1px 0 0 rgba(79, 216, 235, 0.35), -30px 0 60px rgba(0, 0, 0, 0.6);
  color: var(--text);
}

.drawer__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line);
}

.drawer__head h3 {
  margin: 0;
  font-size: 17px;
  letter-spacing: 0.08em;
  text-shadow: 0 0 12px rgba(79, 216, 235, 0.4);
}

.drawer__head p {
  margin: 4px 0 0;
  font-size: 11px;
}

.drawer__close {
  flex: 0 0 auto;
  padding: 0 6px 4px;
  background: transparent;
  border: none;
  color: var(--muted);
  font-size: 22px;
  line-height: 1;
  cursor: pointer;
}

.drawer__close:hover {
  color: var(--cyan);
}

.block {
  margin-top: 16px;
  padding-left: 12px;
  border-left: 1px solid rgba(79, 216, 235, 0.3);
}

.block h4 {
  display: flex;
  gap: 8px;
  margin: 0 0 8px;
  color: var(--cyan);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.18em;
}

.kv {
  display: grid;
  grid-template-columns: 80px 1fr;
  gap: 5px 10px;
  margin: 0;
  font-size: 12px;
}

.kv dt {
  color: var(--muted);
}

.kv dd {
  margin: 0;
  overflow-wrap: anywhere;
}

.rows {
  display: flex;
  flex-direction: column;
}

.row {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
  padding: 6px 0;
  border-bottom: 1px solid rgba(125, 137, 184, 0.1);
  font-size: 12px;
}

.state {
  font-family: var(--mono);
  font-size: 11px;
}

.state--done {
  color: var(--ok);
}

.state--failed {
  color: var(--bad);
}

.state--dispatched {
  color: var(--cyan);
}

.muted {
  color: var(--muted);
}

.mono {
  font-family: var(--mono);
  font-variant-numeric: tabular-nums;
}

.pad {
  padding: 10px 0;
}
</style>
