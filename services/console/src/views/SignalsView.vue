<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { fetchSignals, type Signal } from "../api";
import EmptyState from "../components/EmptyState.vue";
import ErrorBar from "../components/ErrorBar.vue";
import SignalCard from "../components/SignalCard.vue";

interface SignalField {
  label: string;
  value: string;
  mono: boolean;
  wide?: boolean;
}

interface SignalGroup {
  kind: string;
  title: string;
  tone: "bad" | "warn" | "muted";
  items: Signal[];
}

const GROUP_ORDER = [
  "dispatch_timeout",
  "run_failed",
  "node_offline_with_work",
] as const;

const KNOWN_KINDS = new Set<string>(GROUP_ORDER);
const POLL_MS = 10_000;

const signals = ref<Signal[] | null>(null);
const error = ref("");
const refreshing = ref(false);
const lastRefreshAt = ref<number | null>(null);

let pollTimer: ReturnType<typeof setInterval> | undefined;
let requestSeq = 0;

const groups = computed(() => groupSignals(signals.value ?? []));
const countText = computed(() => (signals.value == null ? "—" : `共 ${signals.value.length} 条`));
const refreshText = computed(() =>
  lastRefreshAt.value == null ? "—" : formatStamp(lastRefreshAt.value),
);
const refreshIso = computed(() =>
  lastRefreshAt.value == null ? undefined : new Date(lastRefreshAt.value).toISOString(),
);
const showSkeleton = computed(() => signals.value === null && error.value === "");
const showEmpty = computed(
  () => signals.value !== null && signals.value.length === 0 && error.value === "",
);

function text(value: string | null | undefined): string {
  if (value == null || value.trim() === "") return "—";
  return value;
}

function field(
  label: string,
  value: string | null | undefined,
  mono: boolean,
  wide = false,
): SignalField {
  const item: SignalField = { label, value: text(value), mono };
  if (wide) item.wide = true;
  return item;
}

function formatMinutes(ageS: number | undefined): string {
  if (typeof ageS !== "number" || !Number.isFinite(ageS)) return "—";
  return `${Math.max(0, Math.round(ageS / 60))} 分钟`;
}

function formatStamp(ts: number): string {
  const date = new Date(ts);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function kindOf(signal: Signal): string {
  const kind = signal.kind.trim();
  return kind || "unknown";
}

function toneOf(kind: string): "bad" | "warn" | "muted" {
  if (kind === "dispatch_timeout") return "warn";
  if (kind === "run_failed" || kind === "node_offline_with_work") {
    return "bad";
  }
  return "muted";
}

function titleOf(kind: string): string {
  switch (kind) {
    case "dispatch_timeout":
      return "派单超时";
    case "run_failed":
      return "作业失败";
    case "node_offline_with_work":
      return "节点离线但有在途工单";
    default:
      return kind;
  }
}

function fieldsOf(signal: Signal): SignalField[] {
  switch (kindOf(signal)) {
    case "dispatch_timeout":
      return [
        field("工单 id", signal.ticket_id, true),
        field("节点 id", signal.node_id, true),
        field("已等待", formatMinutes(signal.age_s), true),
      ];
    case "run_failed":
      return [
        field("job", signal.job, true),
        field("day", signal.day, true),
        field("detail", signal.detail, false, true),
      ];
    case "node_offline_with_work":
      return [field("node_id", signal.node_id, true), field("ticket_id", signal.ticket_id, true)];
    default:
      return unknownFields(signal);
  }
}

function unknownFields(signal: Signal): SignalField[] {
  const items: SignalField[] = [];
  if (signal.ticket_id) items.push(field("ticket_id", signal.ticket_id, true));
  if (signal.node_id) items.push(field("node_id", signal.node_id, true));
  if (typeof signal.age_s === "number") items.push(field("已等待", formatMinutes(signal.age_s), true));
  if (signal.job) items.push(field("job", signal.job, true));
  if (signal.day) items.push(field("day", signal.day, true));
  if (signal.detail) items.push(field("detail", signal.detail, false, true));
  if (signal.effect_kind) items.push(field("effect_kind", signal.effect_kind, true));
  if (signal.object) items.push(field("object", signal.object, true, true));
  if (items.length === 0) items.push(field("kind", signal.kind, true));
  return items;
}

function groupSignals(list: Signal[]): SignalGroup[] {
  const buckets = new Map<string, Signal[]>();
  for (const signal of list) {
    const kind = kindOf(signal);
    const found = buckets.get(kind);
    if (found) found.push(signal);
    else buckets.set(kind, [signal]);
  }
  const ordered = [
    ...GROUP_ORDER.filter((kind) => buckets.has(kind)),
    ...[...buckets.keys()].filter((kind) => !KNOWN_KINDS.has(kind)).sort(),
  ];
  return ordered.map((kind) => ({
    kind,
    title: titleOf(kind),
    tone: toneOf(kind),
    items: buckets.get(kind) ?? [],
  }));
}

function errorMessage(err: unknown): string {
  if (err instanceof Error && err.message.trim()) return err.message;
  return "信号加载失败";
}

async function load(): Promise<void> {
  const seq = ++requestSeq;
  const initial = signals.value === null;
  refreshing.value = true;
  if (initial) error.value = "";
  try {
    const result = await fetchSignals();
    if (seq !== requestSeq) return;
    signals.value = Array.isArray(result.signals) ? result.signals : [];
    error.value = "";
    lastRefreshAt.value = Date.now();
  } catch (err: unknown) {
    if (seq !== requestSeq) return;
    error.value = errorMessage(err);
  } finally {
    if (seq === requestSeq) refreshing.value = false;
  }
}

function refresh(): void {
  void load();
  startPoll();
}

function startPoll(): void {
  stopPoll();
  pollTimer = setInterval(() => {
    void load();
  }, POLL_MS);
}

function stopPoll(): void {
  if (pollTimer !== undefined) {
    clearInterval(pollTimer);
    pollTimer = undefined;
  }
}

onMounted(() => {
  void load();
  startPoll();
});

onBeforeUnmount(() => {
  requestSeq += 1;
  stopPoll();
});
</script>

<template>
  <section class="page" :aria-busy="refreshing">
    <header class="signals-head">
      <h2 class="page__title">信号</h2>
      <div class="signals-head__meta">
        <span class="mono">{{ countText }}</span>
        <span class="signals-head__stamp">
          <span class="muted">最近刷新</span>
          <time class="mono" :datetime="refreshIso">{{ refreshText }}</time>
        </span>
        <button type="button" class="refresh" aria-label="刷新信号" :aria-busy="refreshing" @click="refresh">
          刷新
        </button>
      </div>
    </header>

    <ErrorBar v-if="error" :message="error" @retry="refresh" />

    <div v-if="showSkeleton" class="skeleton" aria-busy="true">
      <p class="skeleton__status muted">加载中</p>
      <div aria-hidden="true">
        <div class="skeleton__bar skeleton__bar--title"></div>
        <div class="skeleton__bar"></div>
        <div class="skeleton__bar"></div>
        <div class="skeleton__bar"></div>
      </div>
    </div>

    <EmptyState
      v-else-if="showEmpty"
      title="暂无信号"
      description="派工超时、作业失败、写回失败、节点离线会出现在这里。"
    />

    <div v-else-if="groups.length > 0" class="groups">
      <section v-for="group in groups" :key="group.kind" class="group" :class="`group--${group.tone}`">
        <h3 class="group__head">
          <span class="group__title" :class="`tone-${group.tone}`">{{ group.title }}</span>
          <span class="group__count mono">{{ group.items.length }} 条</span>
        </h3>
        <div class="group__list">
          <SignalCard
            v-for="(signal, index) in group.items"
            :key="`${group.kind}:${signal.id}:${index}`"
            :id="signal.id"
            :tone="group.tone"
            :fields="fieldsOf(signal)"
          />
        </div>
      </section>
    </div>
  </section>
</template>

<style scoped>
.page {
  min-width: 0;
  max-width: 100%;
  overflow-x: clip;
}

.signals-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px 16px;
  min-width: 0;
  margin-bottom: 12px;
}

.signals-head .page__title {
  margin: 0;
}

.signals-head__meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px 12px;
  margin-left: auto;
  min-width: 0;
  color: var(--muted);
}

.signals-head__stamp {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}

.signals-head__stamp time {
  color: #d5def8;
  overflow-wrap: anywhere;
}

.refresh {
  position: relative;
  flex: 0 0 auto;
  padding: 2px 4px 5px;
  background: none;
  border: none;
  color: var(--text);
  letter-spacing: 2px;
  cursor: pointer;
  transition: color 0.18s, text-shadow 0.18s;
}

.refresh::after {
  content: "";
  position: absolute;
  right: 10%;
  bottom: 0;
  left: 10%;
  height: 1px;
  background: linear-gradient(90deg, transparent, currentColor, transparent);
  transform: scaleX(0);
  transition: transform 0.22s ease;
  pointer-events: none;
}

.refresh:hover,
.refresh:focus-visible {
  color: var(--cyan);
  text-shadow: 0 0 10px rgba(79, 216, 235, 0.75);
}

.refresh:hover::after,
.refresh:focus-visible::after {
  transform: scaleX(1);
}

.refresh:focus-visible {
  outline: 1px solid var(--cyan);
  outline-offset: 2px;
}

.refresh[aria-busy="true"] {
  opacity: 0.55;
}

.page :deep(.error-bar) {
  min-width: 0;
  max-width: 100%;
}

.page :deep(.error-bar__text) {
  overflow-wrap: anywhere;
}

.skeleton__status {
  margin: 0 0 8px;
}

.skeleton__bar {
  height: 28px;
  margin-top: 0;
  background: linear-gradient(90deg, rgba(79, 216, 235, 0.05), transparent 72%);
  border: none;
  border-top: 1px solid rgba(125, 137, 184, 0.14);
}

.skeleton__bar--title {
  width: min(180px, 40%);
  height: 12px;
  margin-top: 0;
  margin-bottom: 8px;
  border-top: none;
  background: rgba(79, 216, 235, 0.16);
  box-shadow: 0 0 8px rgba(79, 216, 235, 0.2);
}

.groups {
  display: flex;
  flex-direction: column;
  gap: 18px;
  min-width: 0;
  max-width: 100%;
}

.group {
  min-width: 0;
  max-width: 100%;
}

.group__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px 12px;
  min-width: 0;
  margin: 0 0 2px;
  padding-bottom: 4px;
  font-size: 12px;
  font-weight: 600;
  box-shadow: inset 0 -1px 0 rgba(125, 137, 184, 0.2);
}

.group--warn .group__head {
  box-shadow: inset 0 -1px 0 rgba(245, 185, 66, 0.4);
}

.group--bad .group__head {
  box-shadow: inset 0 -1px 0 rgba(242, 109, 125, 0.4);
}

.group__title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  letter-spacing: 0.14em;
  overflow-wrap: anywhere;
}

.group__title::before {
  content: "";
  width: 6px;
  height: 6px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 6px currentColor;
}

.group__count {
  flex: 0 0 auto;
  color: var(--muted);
  font-weight: 400;
}

.group__list {
  display: flex;
  flex-direction: column;
  gap: 0;
  min-width: 0;
  max-width: 100%;
}

.group__list :deep(.signal:first-child) {
  border-top-width: 0;
}

.tone-bad {
  color: var(--bad);
  text-shadow: 0 0 8px rgba(242, 109, 125, 0.55);
}

.tone-warn {
  color: var(--amber);
  text-shadow: 0 0 8px rgba(245, 185, 66, 0.55);
}

.tone-muted {
  color: var(--muted);
}
</style>
