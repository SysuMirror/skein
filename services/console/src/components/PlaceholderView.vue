<script setup lang="ts">
import EmptyState from "./EmptyState.vue";

defineProps<{
  title: string;
  description?: string;
}>();
</script>

<template>
  <section class="page">
    <h2 class="page__title">{{ title }}</h2>
    <EmptyState title="尚未接入" :description="description" />
  </section>
</template>
