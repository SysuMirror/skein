<script setup lang="ts">
defineProps<{ message: string }>();
const emit = defineEmits<{
  (event: "retry"): void;
}>();
</script>

<template>
  <div class="error-bar" role="alert">
    <span class="error-bar__text">{{ message }}</span>
    <button type="button" class="error-bar__retry" @click="emit('retry')">重试</button>
  </div>
</template>

<style scoped>
.error-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  max-width: 100%;
  margin-bottom: 12px;
  padding: 6px 0 6px 12px;
  background: transparent;
  border: none;
  border-left: 1px solid var(--bad);
  box-shadow: -8px 0 16px -12px var(--bad);
  color: var(--bad);
}

.error-bar__text {
  flex: 1 1 auto;
  min-width: 0;
  overflow-wrap: anywhere;
  text-shadow: 0 0 10px rgba(242, 109, 125, 0.35);
}

.error-bar__retry {
  position: relative;
  flex: 0 0 auto;
  padding: 2px 8px 4px;
  background: transparent;
  border: none;
  color: var(--cyan);
  letter-spacing: 0.08em;
  cursor: pointer;
  transition: color 0.18s, text-shadow 0.18s;
}

.error-bar__retry::after {
  content: "";
  position: absolute;
  right: 10%;
  bottom: 0;
  left: 10%;
  height: 1px;
  background: linear-gradient(90deg, transparent, currentColor, transparent);
  transform: scaleX(0);
  transition: transform 0.22s ease;
}

.error-bar__retry:hover,
.error-bar__retry:focus-visible {
  color: var(--cyan);
  text-shadow: 0 0 8px rgba(79, 216, 235, 0.75);
}

.error-bar__retry:hover::after,
.error-bar__retry:focus-visible::after {
  transform: scaleX(1);
}
</style>
