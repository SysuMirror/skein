<script lang="ts">
export interface FlowStage {
  key: string;
  label: string;
  value: number;
  sub: string;
  tone: "cyan" | "violet" | "amber" | "ok" | "bad" | "muted";
  to: string;
}
</script>

<script setup lang="ts">
// 闭环流水线：反馈 → 准入 → 工单 → 派工 → 交付 → 验收。
// 每一段都是真实计数，点进去就是那一段的列表。
import { useRouter } from "vue-router";
import CountUp from "./CountUp.vue";

defineProps<{
  stages: FlowStage[];
  active: number;
}>();

const router = useRouter();
</script>

<template>
  <nav class="flow" :class="{ 'flow--busy': active > 0 }" aria-label="闭环流水线">
    <div class="flow__rail" aria-hidden="true">
      <i class="flow__comet"></i>
    </div>
    <button
      v-for="(stage, i) in stages"
      :key="stage.key"
      type="button"
      class="stage"
      :class="`stage--${stage.tone}`"
      @click="router.push(stage.to)"
    >
      <span class="stage__idx">{{ String(i + 1).padStart(2, "0") }}</span>
      <span class="stage__label">{{ stage.label }}</span>
      <b class="stage__value"><count-up :value="stage.value" /></b>
      <span class="stage__sub">{{ stage.sub }}</span>
    </button>
  </nav>
</template>

<style scoped>
.flow {
  position: relative;
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;
  min-width: 0;
  margin-bottom: 12px;
  padding: 14px 12px 10px;
  background: linear-gradient(180deg, rgba(79, 216, 235, 0.04), transparent 60%),
    var(--bg-elevated);
  border: 1px solid var(--line);
}

.flow__rail {
  position: absolute;
  top: 30px;
  right: 5%;
  left: 5%;
  height: 1px;
  overflow: hidden;
  background: linear-gradient(90deg, transparent, rgba(79, 216, 235, 0.35), transparent);
}

.flow__comet {
  position: absolute;
  top: -1px;
  left: -64px;
  width: 64px;
  height: 3px;
  background: linear-gradient(90deg, transparent, var(--cyan), transparent);
  box-shadow: 0 0 12px var(--cyan);
  animation: comet 5.2s linear infinite;
}

.flow--busy .flow__comet {
  animation-duration: 2.4s;
  box-shadow: 0 0 18px var(--cyan);
}

.stage {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 1px;
  min-width: 0;
  padding: 6px 10px 4px;
  background: transparent;
  border: none;
  border-left: 1px solid rgba(48, 58, 84, 0.8);
  color: var(--text);
  text-align: left;
  cursor: pointer;
}

.stage::before {
  content: "";
  position: absolute;
  top: -8px;
  left: -3px;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 8px currentColor;
  opacity: 0.7;
}

.stage:hover,
.stage:focus-visible {
  background: rgba(79, 216, 235, 0.05);
  outline: none;
}

.stage:hover .stage__value,
.stage:focus-visible .stage__value {
  text-shadow: 0 0 18px currentColor;
}

.stage__idx {
  color: var(--muted);
  font-family: var(--mono);
  font-size: 10px;
  letter-spacing: 0.2em;
}

.stage__label {
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 0.14em;
}

.stage__value {
  color: currentColor;
  font-family: var(--mono);
  font-size: 24px;
  line-height: 1.15;
  font-weight: 700;
}

.stage__sub {
  color: var(--muted);
  font-size: 10px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}

.stage--cyan {
  color: var(--cyan);
}

.stage--violet {
  color: var(--violet);
}

.stage--amber {
  color: var(--amber);
}

.stage--ok {
  color: var(--ok);
}

.stage--bad {
  color: var(--bad);
}

.stage--muted {
  color: var(--muted);
}

@keyframes comet {
  from {
    left: -64px;
  }

  to {
    left: 100%;
  }
}

@media (prefers-reduced-motion: reduce) {
  .flow__comet {
    animation: none;
    opacity: 0.4;
  }
}

@media (max-width: 1080px) {
  .flow {
    grid-template-columns: repeat(3, minmax(0, 1fr));
    row-gap: 10px;
  }

  .flow__rail {
    display: none;
  }
}

@media (max-width: 620px) {
  .flow {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
