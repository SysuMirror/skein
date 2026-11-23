<script setup lang="ts">
defineProps<{
  title: string;
  description?: string;
}>();
</script>

<template>
  <div class="empty">
    <p class="empty__title">{{ title }}</p>
    <p v-if="description" class="empty__desc">{{ description }}</p>
  </div>
</template>

<style scoped>
.empty {
  padding: 28px 12px;
  text-align: center;
  background: transparent;
}

.empty__title {
  margin: 0;
  color: var(--text);
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.14em;
  text-shadow: 0 0 10px rgba(79, 216, 235, 0.28);
}

.empty__desc {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 12px;
}
</style>
