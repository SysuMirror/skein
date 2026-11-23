<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import {
  fetchLLMCalls,
  fetchLLMSessions,
  type LLMCallRecord,
  type LLMSessionRecord,
} from "../api";
import EmptyState from "../components/EmptyState.vue";
import ErrorBar from "../components/ErrorBar.vue";

const sessions = ref<LLMSessionRecord[]>([]);
const calls = ref<LLMCallRecord[]>([]);
const selected = ref("");
const loading = ref(false);
const callsLoading = ref(false);
const error = ref<string | null>(null);
let timer: number | undefined;
let alive = true;

function stamp(ts: number): string {
  const date = new Date(ts * 1000);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}`;
}

async function loadSessions() {
  if (loading.value) return;
  loading.value = true;
  error.value = null;
  try {
    const body = await fetchLLMSessions(100);
    if (!alive) return;
    sessions.value = body.sessions;
    if (!selected.value && sessions.value.length) {
      await open(sessions.value[0].session_id);
    } else if (selected.value) {
      await open(selected.value);
    }
  } catch (err) {
    if (alive) error.value = err instanceof Error ? err.message : "会话加载失败";
  } finally {
    loading.value = false;
  }
}

async function open(sessionId: string) {
  selected.value = sessionId;
  callsLoading.value = true;
  try {
    const body = await fetchLLMCalls(sessionId, 200);
    if (alive) calls.value = body.calls;
  } catch (err) {
    if (alive) error.value = err instanceof Error ? err.message : "调用加载失败";
  } finally {
    callsLoading.value = false;
  }
}

onMounted(() => {
  void loadSessions();
  timer = window.setInterval(() => {
    void loadSessions();
  }, 10000);
});

onBeforeUnmount(() => {
  alive = false;
  if (timer !== undefined) window.clearInterval(timer);
});
</script>

<template>
  <section class="calls">
    <header class="calls__head">
      <div>
        <h2>会话</h2>
        <p class="calls__sub">
          每次对话和模型调用 · {{ sessions.length }} 个会话
        </p>
      </div>
      <button type="button" class="ghost" :disabled="loading" @click="loadSessions">
        {{ loading ? "刷新中" : "刷新" }}
      </button>
    </header>

    <ErrorBar v-if="error" :message="error" @retry="loadSessions" />

    <div class="calls__split">
      <aside class="calls__sessions">
        <button
          v-for="session in sessions"
          :key="session.session_id"
          type="button"
          class="session"
          :class="{ 'session--active': session.session_id === selected }"
          @click="open(session.session_id)"
        >
          <span class="session__id mono">{{ session.session_id.slice(0, 18) }}</span>
          <span class="session__meta">
            {{ session.source || "chat" }} · {{ session.calls }} 次 · {{ session.tokens }} tokens
          </span>
          <span class="session__meta">
            {{ session.actor || "匿名" }} · {{ stamp(session.last_at) }}
          </span>
        </button>
        <EmptyState
          v-if="!sessions.length && !loading"
          title="还没有调用"
          description="Agent 或外部服务调用过模型之后，这里会出现每一次调用的留痕。"
        />
      </aside>

      <div class="calls__detail">
        <div v-if="!selected" class="calls__hint">选择左侧会话</div>
        <template v-else>
          <p class="calls__hint mono">{{ selected }} · {{ calls.length }} 次调用</p>
          <article v-for="call in calls" :key="call.id" class="call">
            <div class="call__head">
              <span class="mono">{{ call.model || "unknown" }}</span>
              <span class="tag">{{ call.source || "chat" }}</span>
              <span>{{ call.latency_ms }}ms</span>
              <span>{{ call.prompt_tokens + call.completion_tokens }} tokens</span>
              <span class="tag" :class="call.status === 'ok' ? 'tag--ok' : 'tag--bad'">
                {{ call.status }}
              </span>
              <span class="muted">{{ stamp(call.created_at) }}</span>
            </div>
            <details>
              <summary>输入</summary>
              <pre>{{ call.prompt || "（空）" }}</pre>
            </details>
            <details>
              <summary>输出</summary>
              <pre>{{ call.response || call.error || "（空）" }}</pre>
            </details>
          </article>
          <p v-if="callsLoading" class="calls__hint">加载中…</p>
        </template>
      </div>
    </div>
  </section>
</template>

<style scoped>
.calls {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.calls__head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}

.calls__head h2 {
  margin: 0;
  color: var(--text);
  font-size: 18px;
  letter-spacing: 0.12em;
}

.calls__sub {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 12px;
}

.calls__split {
  display: grid;
  grid-template-columns: minmax(220px, 320px) minmax(0, 1fr);
  gap: 16px;
  min-height: 60vh;
}

.calls__sessions {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 72vh;
  overflow: auto;
  padding-right: 4px;
}

.session {
  display: flex;
  flex-direction: column;
  gap: 5px;
  width: 100%;
  padding: 10px 12px;
  border: 1px solid transparent;
  border-left: 2px solid transparent;
  background: transparent;
  color: var(--text);
  text-align: left;
  cursor: pointer;
}

.session:hover,
.session:focus-visible {
  border-color: var(--line);
  border-left-color: var(--cyan);
  background: var(--bg-elevated);
}

.session--active {
  border-color: var(--line);
  border-left-color: var(--cyan);
  background: var(--bg-elevated);
}

.session__id {
  color: var(--cyan);
  font-size: 12px;
}

.session__meta {
  color: var(--muted);
  font-size: 11px;
}

.calls__detail {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
  max-height: 72vh;
  overflow: auto;
  padding-right: 4px;
}

.calls__hint {
  margin: 0;
  color: var(--muted);
  font-size: 12px;
}

.call {
  padding: 12px 14px;
  border: 1px solid var(--line);
  background: var(--bg-elevated);
}

.call__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  color: var(--muted);
  font-size: 11px;
}

.call__head span:first-child {
  color: var(--text);
  font-size: 12px;
}

.tag {
  padding: 1px 6px;
  border: 1px solid var(--line);
  color: var(--muted);
  font-size: 10px;
}

.tag--ok {
  border-color: rgba(87, 217, 163, 0.45);
  color: var(--ok);
}

.tag--bad {
  border-color: rgba(242, 109, 125, 0.45);
  color: var(--bad);
}

details {
  margin-top: 8px;
}

summary {
  color: var(--muted);
  font-size: 11px;
  cursor: pointer;
}

pre {
  max-height: 260px;
  margin: 6px 0 0;
  padding: 10px;
  overflow: auto;
  border: 1px solid var(--line);
  background: var(--bg);
  color: var(--text);
  font-family: var(--mono);
  font-size: 11px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 900px) {
  .calls__split {
    grid-template-columns: 1fr;
  }
  .calls__sessions {
    max-height: 32vh;
  }
  .calls__detail {
    max-height: none;
  }
}
</style>
