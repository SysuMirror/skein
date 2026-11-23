<script setup lang="ts">
import { nextTick, onMounted, ref } from "vue";
import {
  fetchAgentThread,
  sendAgentMessage,
  type AgentMessageRecord,
} from "../api";
import ErrorBar from "../components/ErrorBar.vue";
import { useConsoleStore } from "../store";

const THREAD_KEY = "skein-agent-thread";
const SUGGESTIONS = [
  "现在有什么要我处理？",
  "哪张单卡住了？",
  "节点都在吗？",
  "最近有哪些模型调用失败？",
];

const store = useConsoleStore();
const threadId = ref("");
const messages = ref<AgentMessageRecord[]>([]);
const draft = ref("");
const loading = ref(false);
const error = ref<string | null>(null);
const scroller = ref<HTMLElement | null>(null);

function newThread(): string {
  return `agent-${Date.now()}`;
}

function stamp(value?: number): string {
  if (!value) return "";
  const date = new Date(value * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}`;
}

async function scrollBottom() {
  await nextTick();
  if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight;
}

async function loadThread() {
  const stored = localStorage.getItem(THREAD_KEY);
  threadId.value = stored || newThread();
  localStorage.setItem(THREAD_KEY, threadId.value);
  if (!stored) return;
  try {
    const body = await fetchAgentThread(threadId.value);
    messages.value = body.messages;
    await scrollBottom();
  } catch {
    // 新线程或旧线程不存在：从空会话开始，不打扰用户。
    messages.value = [];
  }
}

async function send(text?: string) {
  const message = (text ?? draft.value).trim();
  if (!message || loading.value) return;
  if (!threadId.value) threadId.value = newThread();
  localStorage.setItem(THREAD_KEY, threadId.value);
  error.value = null;
  messages.value.push({ role: "user", content: message });
  draft.value = "";
  loading.value = true;
  await scrollBottom();
  try {
    const body = await sendAgentMessage(threadId.value, message);
    messages.value.push({ role: "assistant", content: body.reply });
    if (body.thread_id) {
      threadId.value = body.thread_id;
      localStorage.setItem(THREAD_KEY, body.thread_id);
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Agent 调用失败";
    messages.value.push({
      role: "assistant",
      content: "这次没连上模型或控制面，稍后再试。",
    });
  } finally {
    loading.value = false;
    await scrollBottom();
  }
}

function reset() {
  threadId.value = newThread();
  localStorage.setItem(THREAD_KEY, threadId.value);
  messages.value = [];
  draft.value = "";
  error.value = null;
}

onMounted(() => {
  void loadThread();
});
</script>

<template>
  <section v-if="!store.can('agent.chat')" class="agent">
    <header class="agent__head">
      <div>
        <h2>雁阵 Agent</h2>
        <p class="agent__sub">
          当前账号没有使用 Agent 的权限：Agent 以你的名义查询和操作，只对管理员开放。
        </p>
      </div>
    </header>
  </section>
  <section v-else class="agent">
    <header class="agent__head">
      <div>
        <h2>雁阵 Agent</h2>
        <p class="agent__sub">
          用自然语言查工单、节点、运行和模型调用；派工/取消只在明确要求时执行。
        </p>
      </div>
      <button type="button" class="ghost" @click="reset">新会话</button>
    </header>

    <ErrorBar v-if="error" :message="error" @retry="() => send()" />

    <div ref="scroller" class="agent__body">
      <div v-if="!messages.length" class="agent__welcome">
        <p class="agent__eyebrow">后厂控制台</p>
        <h3>今天要处理什么？</h3>
        <p class="muted">
          我只读雁阵的真实状态；不会自动合入、不会自动验收、不会删资源。
        </p>
        <div class="agent__suggestions">
          <button
            v-for="item in SUGGESTIONS"
            :key="item"
            type="button"
            @click="send(item)"
          >
            {{ item }}
          </button>
        </div>
      </div>

      <article
        v-for="(message, index) in messages"
        :key="index"
        class="bubble"
        :class="message.role === 'user' ? 'bubble--user' : 'bubble--agent'"
      >
        <div class="bubble__meta">
          {{ message.role === "user" ? "你" : "雁阵 Agent" }}
          <span v-if="message.created_at">{{ stamp(message.created_at) }}</span>
        </div>
        <div class="bubble__text">{{ message.content }}</div>
      </article>

      <article v-if="loading" class="bubble bubble--agent">
        <div class="bubble__meta">雁阵 Agent</div>
        <div class="bubble__text muted">查一下…</div>
      </article>
    </div>

    <form class="agent__compose" @submit.prevent="send()">
      <textarea
        v-model="draft"
        rows="2"
        placeholder="例如：SK-xxxx 到哪了？ / 现在哪张单卡住了？ / 把 SK-xxxx 派给 n-cloud-skein"
        :disabled="loading"
        @keydown.enter.exact.prevent="send()"
      ></textarea>
      <button type="submit" :disabled="loading || !draft.trim()">
        {{ loading ? "处理中" : "发送" }}
      </button>
    </form>
  </section>
</template>

<style scoped>
.agent {
  display: flex;
  flex-direction: column;
  gap: 14px;
  height: calc(100vh - 190px);
  min-height: 420px;
}

.agent__head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}

.agent__head h2 {
  margin: 0;
  color: var(--text);
  font-size: 18px;
  letter-spacing: 0.12em;
}

.agent__sub {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 12px;
}

.agent__body {
  flex: 1;
  overflow: auto;
  padding: 18px 16px;
  border: 1px solid var(--line);
  background: linear-gradient(180deg, rgba(10, 16, 36, 0.9), rgba(4, 6, 14, 0.95));
}

.agent__welcome {
  max-width: 560px;
  margin: 6vh auto 0;
  text-align: center;
}

.agent__eyebrow {
  margin: 0 0 8px;
  color: var(--cyan);
  font-size: 10px;
  letter-spacing: 0.24em;
  text-transform: uppercase;
}

.agent__welcome h3 {
  margin: 0;
  color: var(--text);
  font-size: 22px;
  font-weight: 600;
}

.agent__suggestions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
  margin-top: 18px;
}

.agent__suggestions button {
  padding: 7px 12px;
  border: 1px solid var(--line);
  background: transparent;
  color: var(--muted);
  font-size: 12px;
  cursor: pointer;
}

.agent__suggestions button:hover,
.agent__suggestions button:focus-visible {
  border-color: var(--cyan);
  color: var(--cyan);
}

.bubble {
  max-width: 78%;
  margin-bottom: 14px;
  padding: 10px 13px;
  border: 1px solid var(--line);
  background: var(--bg-elevated);
}

.bubble--user {
  margin-left: auto;
  border-color: rgba(79, 216, 235, 0.4);
  background: rgba(79, 216, 235, 0.08);
}

.bubble--agent {
  margin-right: auto;
}

.bubble__meta {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 6px;
  color: var(--muted);
  font-size: 10px;
  letter-spacing: 0.08em;
}

.bubble__text {
  color: var(--text);
  font-size: 13px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}

.agent__compose {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  align-items: end;
}

.agent__compose textarea {
  width: 100%;
  resize: none;
  padding: 10px 12px;
  border: 1px solid var(--line);
  background: var(--bg-elevated);
  color: var(--text);
  font-family: var(--sans);
  font-size: 13px;
  line-height: 1.5;
}

.agent__compose textarea:focus {
  outline: none;
  border-color: var(--cyan);
}

.agent__compose button {
  padding: 10px 18px;
  border: 1px solid rgba(79, 216, 235, 0.5);
  background: rgba(79, 216, 235, 0.12);
  color: var(--cyan);
  font-size: 13px;
  cursor: pointer;
}

.agent__compose button:disabled {
  opacity: 0.45;
  cursor: default;
}

@media (max-width: 760px) {
  .agent {
    height: auto;
  }
  .agent__body {
    min-height: 50vh;
  }
  .bubble {
    max-width: 92%;
  }
}
</style>
