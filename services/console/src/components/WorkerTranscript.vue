<script setup lang="ts">
import { computed } from "vue";
import type { TurnRecord } from "../api";
import EmptyState from "./EmptyState.vue";
import ErrorBar from "./ErrorBar.vue";

const props = defineProps<{
  sessionId: string;
  nodeId: string | null;
  ticketId: string | null;
  absent: boolean;
  loading: boolean;
  error: string;
  turns: TurnRecord[] | null;
}>();

const emit = defineEmits<{
  (event: "retry"): void;
}>();

const showLoading = computed(() => props.loading && props.turns == null);

function formatUnix(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return "—";
  const date = new Date(seconds * 1000);
  if (Number.isNaN(date.getTime())) return "—";
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value < 0) return "—";
  return `${Math.floor(value).toLocaleString("zh-CN")} 字节`;
}

function hasBody(turn: TurnRecord): boolean {
  return typeof turn.text === "string" && turn.text.trim().length > 0;
}

function display(value: string | null | undefined): string {
  const text = value?.trim();
  return text ? text : "—";
}
</script>

<template>
  <section class="transcript" aria-live="polite">
    <header class="transcript__head">
      <h3 class="mono">{{ sessionId }}</h3>
      <p class="transcript__where">
        <span>节点 {{ nodeId || "—" }}</span>
        <span>工单 {{ ticketId || "—" }}</span>
      </p>
      <p v-if="absent" class="is-warn">当前列表里已经没有这个会话。</p>
    </header>

    <div v-if="showLoading" class="loading" aria-busy="true">
      <p class="muted">正在读取轮次</p>
      <div class="bar"></div>
      <div class="bar bar--short"></div>
      <div class="bar"></div>
    </div>

    <template v-else>
      <ErrorBar v-if="error" :message="error" @retry="emit('retry')" />
      <p v-if="loading" class="muted">正在刷新轮次</p>
      <EmptyState v-if="turns && turns.length === 0 && !error" title="尚无轮次" />
      <ol v-else-if="turns && turns.length" class="turns">
        <li v-for="turn in turns" :key="`${turn.turn_index}-${turn.sha256}`" class="turn">
          <dl class="kv">
            <div>
              <dt>轮次</dt>
              <dd class="mono">{{ turn.turn_index }}</dd>
            </div>
            <div>
              <dt>时间</dt>
              <dd class="mono">{{ formatUnix(turn.created_at) }}</dd>
            </div>
            <div>
              <dt>分支</dt>
              <dd class="mono">{{ display(turn.branch) }}</dd>
            </div>
            <div>
              <dt>head</dt>
              <dd class="mono">{{ display(turn.head_sha) }}</dd>
            </div>
            <div>
              <dt>字节</dt>
              <dd class="mono">{{ formatBytes(turn.bytes) }}</dd>
            </div>
          </dl>
          <details v-if="hasBody(turn)" class="body">
            <summary>正文</summary>
            <pre>{{ turn.text }}</pre>
          </details>
          <p v-else class="muted body-missing">无正文</p>
        </li>
      </ol>
    </template>
  </section>
</template>

<style scoped>
.transcript {
  min-width: 0;
  max-width: 100%;
}

.transcript__head {
  min-width: 0;
  margin-bottom: 12px;
}

.transcript__head h3 {
  margin: 0;
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.02em;
  overflow-wrap: anywhere;
  text-shadow: 0 0 10px rgba(79, 216, 235, 0.4);
}

.transcript__where {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  margin: 4px 0 0;
  color: var(--muted);
  font-family: var(--mono);
  font-size: 11px;
}

.transcript__where span {
  min-width: 0;
  max-width: 100%;
  overflow-wrap: anywhere;
}

.is-warn {
  margin: 6px 0 0;
  color: var(--warn);
  text-shadow: 0 0 8px rgba(245, 185, 66, 0.35);
}

.loading {
  min-width: 0;
}

.bar {
  height: 1px;
  margin-top: 14px;
  background: linear-gradient(90deg, rgba(79, 216, 235, 0.5), transparent 80%);
}

.bar--short {
  width: 46%;
}

.turns {
  margin: 0;
  padding: 0;
  list-style: none;
  min-width: 0;
}

.turn {
  position: relative;
  min-width: 0;
  padding: 8px 0 10px 16px;
}

.turn::before {
  content: "";
  position: absolute;
  top: 0;
  bottom: 0;
  left: 2px;
  width: 1px;
  background: linear-gradient(180deg, rgba(79, 216, 235, 0.45), rgba(125, 137, 184, 0.18));
}

.turn:first-child::before {
  top: 14px;
}

.turn:last-child::before {
  bottom: auto;
  height: 16px;
}

.turn:only-child::before {
  display: none;
}

.turn::after {
  content: "";
  position: absolute;
  top: 13px;
  left: -1px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--cyan);
  box-shadow: 0 0 6px var(--cyan);
}

.kv {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  margin: 0;
}

.kv div {
  min-width: 0;
  max-width: 100%;
}

.kv dt {
  color: var(--muted);
  font-size: 11px;
  letter-spacing: 0.08em;
}

.kv dd {
  margin: 0;
  font-size: 12px;
  overflow-wrap: anywhere;
}

.body {
  min-width: 0;
  margin-top: 6px;
}

.body summary {
  cursor: pointer;
  color: var(--cyan);
  letter-spacing: 0.08em;
  text-shadow: 0 0 8px rgba(79, 216, 235, 0.4);
}

.body summary:hover {
  color: #fff;
}

.body pre {
  max-height: 220px;
  max-width: 100%;
  margin: 6px 0 0;
  padding: 10px 12px;
  overflow: auto;
  border: none;
  background: rgba(2, 4, 12, 0.72);
  box-shadow: inset 1px 0 0 rgba(79, 216, 235, 0.28);
  color: #a8b6e8;
  font-family: var(--mono);
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.body-missing {
  margin: 6px 0 0;
  font-family: var(--mono);
  font-size: 11px;
}
</style>
