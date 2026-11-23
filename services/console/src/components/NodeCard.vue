<script setup lang="ts">
import { computed } from "vue";
import StatusTag from "./StatusTag.vue";

const props = defineProps<{
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
}>();

const emit = defineEmits<{
  (event: "select", nodeId: string): void;
}>();

const tone = computed(() => {
  const value = props.status.toLowerCase();
  if (["ok", "online", "done", "succeeded", "success", "active", "approved"].includes(value)) {
    return "ok";
  }
  if (["warn", "warning", "review", "pending", "staged", "running"].includes(value)) {
    return "warn";
  }
  if (["bad", "offline", "rejected", "failed", "error", "unauthorized"].includes(value)) {
    return "bad";
  }
  if (value === "info") return "info";
  return "neutral";
});

const rawCode = computed(() => props.statusLabel === props.status && props.status !== "neutral");
</script>

<template>
  <article
    class="card"
    :class="`is-${tone}`"
    role="button"
    tabindex="0"
    @click="emit('select', props.nodeId)"
    @keydown.enter.prevent="emit('select', props.nodeId)"
    @keydown.space.prevent="emit('select', props.nodeId)"
  >
    <div class="card__top">
      <h3 class="card__title">{{ title }}</h3>
      <span class="card__status" :title="statusTitle || undefined">
        <StatusTag :class="{ 'is-code': rawCode }" :status="status" :label="statusLabel" />
        <span v-if="statusCode" class="status-code mono">{{ statusCode }}</span>
      </span>
    </div>
    <p v-if="title !== nodeId" class="card__id mono">{{ nodeId }}</p>
    <dl class="meta">
      <dt>主机</dt>
      <dd class="mono meta__fine">{{ host }}</dd>
      <dt>平台</dt>
      <dd>
        <ul
          v-if="platforms.length"
          class="tags"
          :title="platformsInvalid ? 'platforms 不是合法 JSON，显示原文' : undefined"
        >
          <li
            v-for="(tag, index) in platforms"
            :key="`${index}-${tag}`"
            class="tag"
            :class="{ 'tag--invalid': platformsInvalid }"
          >
            {{ tag }}
          </li>
        </ul>
        <span v-else class="muted">未声明</span>
      </dd>
      <dt>容量</dt>
      <dd class="mono" title="max_workers">{{ capacity }}</dd>
      <dt>最近心跳</dt>
      <dd class="mono meta__fine" :class="{ 'is-stale': heartbeatStale }" :title="heartbeatTitle || undefined">
        {{ heartbeat }}
      </dd>
    </dl>
  </article>
</template>

<style scoped>
.card {
  --bar: var(--muted);
  position: relative;
  min-width: 0;
  padding: 12px 4px 12px 14px;
  border: none;
  background: transparent;
  cursor: pointer;
}

.card:focus-visible {
  outline: 1px solid var(--cyan);
  outline-offset: 2px;
}

.card:hover .card__title {
  color: #fff;
  text-shadow: 0 0 10px rgba(79, 216, 235, 0.55);
}

.card::before {
  content: "";
  position: absolute;
  top: 10px;
  bottom: 10px;
  left: 0;
  width: 2px;
  background: var(--bar);
  box-shadow: 0 0 8px var(--bar);
}

.card.is-ok {
  --bar: var(--ok);
}

.card.is-warn {
  --bar: var(--warn);
}

.card.is-bad {
  --bar: var(--bad);
}

.card.is-info {
  --bar: var(--accent);
}

.card:hover {
  background: linear-gradient(90deg, color-mix(in srgb, var(--bar) 9%, transparent), transparent 76%);
}

.card__top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}

.card__title {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
  color: #fff;
  font-size: 15px;
  font-weight: 700;
  line-height: 1.35;
  letter-spacing: 0.04em;
  text-shadow: 0 0 10px rgba(79, 216, 235, 0.45);
}

.card:hover .card__title {
  text-shadow: 0 0 12px rgba(79, 216, 235, 0.75);
}

.card__status {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  flex: 0 1 auto;
  min-width: 0;
  max-width: 48%;
}

.card__status :deep(.status-tag.is-code) {
  font-family: var(--mono);
  font-size: 11px;
}

.status-code {
  min-width: 0;
  overflow: hidden;
  color: var(--muted);
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card__id {
  margin: 2px 0 0;
  color: var(--muted);
  font-size: 11px;
  overflow-wrap: anywhere;
}

.meta {
  display: grid;
  grid-template-columns: 4.5em minmax(0, 1fr);
  gap: 3px 8px;
  margin: 8px 0 0;
}

.meta dt,
.meta dd {
  min-width: 0;
  margin: 0;
  overflow-wrap: anywhere;
}

.meta dt {
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 0.08em;
}

.meta__fine {
  color: var(--muted);
  font-size: 11px;
  line-height: 1.45;
}

.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 10px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.tag {
  position: relative;
  max-width: 100%;
  padding-left: 11px;
  border: none;
  color: var(--cyan);
  font-family: var(--mono);
  font-size: 11px;
  line-height: 1.6;
  overflow-wrap: anywhere;
  text-shadow: 0 0 8px rgba(79, 216, 235, 0.4);
}

.tag::before {
  content: "";
  position: absolute;
  top: 0.48em;
  left: 0;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 6px currentColor;
}

.tag--invalid {
  color: var(--warn);
  text-shadow: 0 0 8px rgba(245, 185, 66, 0.45);
}

.is-stale {
  color: var(--warn);
  text-shadow: 0 0 8px rgba(245, 185, 66, 0.4);
}
</style>
