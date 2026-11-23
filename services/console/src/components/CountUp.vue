<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";

const props = withDefaults(defineProps<{ value: number; duration?: number }>(), {
  duration: 620,
});

const shown = ref(props.value);
let raf = 0;
let settle = 0;

watch(
  () => props.value,
  (to, from) => {
    cancelAnimationFrame(raf);
    window.clearTimeout(settle);
    // 后台标签页不跑 rAF，直接跳到目标值，避免停在上一个数字。
    if (
      document.hidden ||
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      shown.value = to;
      return;
    }
    const started = performance.now();
    const step = (now: number) => {
      const progress = Math.min(1, (now - started) / props.duration);
      const eased = 1 - Math.pow(1 - progress, 3);
      shown.value = Math.round(from + (to - from) * eased);
      if (progress < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    // 窗口被遮挡时 rAF 会被节流；兜底到点就落值。
    settle = window.setTimeout(() => {
      shown.value = to;
    }, props.duration + 250);
  },
);

onBeforeUnmount(() => {
  cancelAnimationFrame(raf);
  window.clearTimeout(settle);
});
</script>

<template>
  <span class="mono">{{ shown }}</span>
</template>
