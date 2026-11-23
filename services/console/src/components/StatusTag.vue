<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  status: string;
  label?: string;
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

const text = computed(() => props.label ?? props.status);
</script>

<template>
  <span class="status-tag" :class="`status-tag--${tone}`">{{ text }}</span>
</template>

<style scoped>
.status-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.4;
  white-space: nowrap;
}

.status-tag::before {
  content: "";
  width: 6px;
  height: 6px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 6px currentColor;
}

.status-tag--ok {
  color: var(--ok);
  text-shadow: 0 0 8px color-mix(in srgb, var(--ok) 55%, transparent);
}

.status-tag--warn {
  color: var(--warn);
  text-shadow: 0 0 8px color-mix(in srgb, var(--warn) 55%, transparent);
}

.status-tag--bad {
  color: var(--bad);
  text-shadow: 0 0 8px color-mix(in srgb, var(--bad) 55%, transparent);
}

.status-tag--info {
  color: var(--accent);
  text-shadow: 0 0 8px color-mix(in srgb, var(--accent) 55%, transparent);
}

.status-tag--neutral {
  color: var(--muted);
}
</style>
