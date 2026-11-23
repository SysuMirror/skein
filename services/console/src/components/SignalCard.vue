<script setup lang="ts">
import { computed } from "vue";

interface SignalField {
  label: string;
  value: string;
  mono: boolean;
  wide?: boolean;
}

const props = defineProps<{
  id: string;
  tone: "bad" | "warn" | "muted";
  fields: SignalField[];
}>();

const shownId = computed(() => (props.id.trim() ? props.id : "—"));
</script>

<template>
  <article class="signal" :class="`signal--${tone}`">
    <div class="signal__row">
      <div class="field field--id">
        <span class="field__label">id</span>
        <span class="field__value mono" :title="shownId">{{ shownId }}</span>
      </div>
      <div
        v-for="field in fields"
        :key="field.label"
        class="field"
        :class="{ 'field--wide': field.wide }"
      >
        <span class="field__label">{{ field.label }}</span>
        <span class="field__value" :class="{ mono: field.mono }" :title="field.mono ? field.value : undefined">{{ field.value }}</span>
      </div>
    </div>
  </article>
</template>

<style scoped>
.signal {
  min-width: 0;
  max-width: 100%;
  padding: 7px 4px;
  border: none;
  border-top: 1px solid rgba(125, 137, 184, 0.16);
  background: transparent;
  transition: background 0.15s ease;
}

.signal:hover {
  background: linear-gradient(90deg, rgba(79, 216, 235, 0.08), transparent 72%);
}

.signal--warn {
  border-top-color: rgba(245, 185, 66, 0.2);
}

.signal--warn:hover {
  background: linear-gradient(90deg, rgba(245, 185, 66, 0.08), transparent 72%);
}

.signal--bad {
  border-top-color: rgba(242, 109, 125, 0.2);
}

.signal--bad:hover {
  background: linear-gradient(90deg, rgba(242, 109, 125, 0.08), transparent 72%);
}

.signal__row {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 3px 16px;
  min-width: 0;
  max-width: 100%;
}

.field {
  display: flex;
  align-items: baseline;
  gap: 6px;
  flex: 1 1 240px;
  min-width: 0;
  max-width: 100%;
}

.field--id {
  flex-basis: 280px;
}

.field--wide {
  flex-basis: 100%;
}

.field__label {
  flex: 0 0 auto;
  color: var(--muted);
  font-size: 11px;
  letter-spacing: 0.08em;
}

.signal--bad .field--id .field__label {
  color: var(--bad);
  text-shadow: 0 0 6px rgba(242, 109, 125, 0.45);
}

.signal--warn .field--id .field__label {
  color: var(--amber);
  text-shadow: 0 0 6px rgba(245, 185, 66, 0.45);
}

.field__value {
  min-width: 0;
  overflow: visible;
  white-space: normal;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.mono {
  font-family: var(--mono);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: #d5def8;
}

.field--wide .field__value {
  white-space: pre-wrap;
}
</style>
