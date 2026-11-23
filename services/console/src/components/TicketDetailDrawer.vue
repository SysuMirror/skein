<script setup lang="ts">
import { computed } from "vue";
import { NDrawer, NDrawerContent, NSkeleton } from "naive-ui";
import type { TicketDetailResponse } from "../api";
import ErrorBar from "./ErrorBar.vue";
import StatusTag from "./StatusTag.vue";

const props = defineProps<{
  show: boolean;
  loading: boolean;
  error: string | null;
  detail: TicketDetailResponse | null;
  pending: "approve" | "reject" | "dispatch" | "cancel" | null;
  // 只读账号（member）：操作按钮不出现
  readonly?: boolean;
}>();

const emit = defineEmits<{
  "update:show": [value: boolean];
  retry: [];
  approve: [];
  reject: [];
  dispatch: [];
  cancel: [];
}>();

const STATUS_LABEL: Record<string, string> = {
  staged: "暂存",
  assigned: "已派发",
  running: "进行中",
  review: "待验收",
  done: "完成",
  rejected: "已驳回",
};

const mode = computed(() => {
  const status = props.detail?.ticket.status;
  if (!status) return "none" as const;
  if (status === "review") return "review" as const;
  if (status === "done" || status === "rejected") return "none" as const;
  return "dispatch" as const;
});

interface Delivery {
  branch?: string;
  base_commit?: string;
  commits?: string;
  diffstat?: string;
  pushed?: boolean;
  summary?: string;
}

const delivery = computed<Delivery | null>(() => {
  const item = props.detail?.assignments.find((row) => row.result);
  if (!item?.result) return null;
  try {
    const data = JSON.parse(item.result) as Delivery;
    return data && typeof data === "object" ? data : null;
  } catch {
    return null;
  }
});

const prDispatch = computed(() => {
  return props.detail?.dispatches.find((row) => row.pr_url) ?? null;
});

const prUrl = computed(() => prDispatch.value?.pr_url ?? "");

const prBase = computed(() => prDispatch.value?.pr_base ?? "");

const testEnvUrl = computed(() => {
  const dispatches = props.detail?.dispatches ?? [];
  const platform =
    prDispatch.value?.platform ||
    dispatches.find((row) => row.platform)?.platform ||
    "";
  if (!platform) return "";
  return props.detail?.test_envs?.[platform] ?? "";
});

function statusText(status: string): string {
  return STATUS_LABEL[status] ?? status;
}

function formatStamp(ts: number | null | undefined): string {
  if (ts == null || !Number.isFinite(ts)) return "无";
  // 控制面时间是 Unix 秒。
  const ms = ts < 1e12 ? ts * 1000 : ts;
  const date = new Date(ms);
  if (Number.isNaN(date.getTime())) return "无";
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function stampIso(ts: number | null | undefined): string | undefined {
  if (ts == null || !Number.isFinite(ts)) return undefined;
  const ms = ts < 1e12 ? ts * 1000 : ts;
  const date = new Date(ms);
  if (Number.isNaN(date.getTime())) return undefined;
  return date.toISOString();
}

function formatJson(value: unknown): string {
  if (value == null) return "无";
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) return "无";
    try {
      return JSON.stringify(JSON.parse(trimmed) as unknown, null, 2);
    } catch {
      return value;
    }
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function textOrEmpty(value: string | null | undefined): string {
  if (value == null || value.trim() === "") return "无";
  return value;
}
</script>

<template>
  <n-drawer
    class="ticket-drawer"
    :show="show"
    :width="600"
    placement="right"
    :auto-focus="false"
    @update:show="emit('update:show', $event)"
  >
    <n-drawer-content
      title="工单详情"
      closable
      :native-scrollbar="true"
      :body-content-style="{ padding: '12px 16px 20px' }"
    >
      <div v-if="loading && !detail" class="state" aria-busy="true">
        <n-skeleton text :repeat="8" :animated="false" />
      </div>
      <error-bar v-if="error" :message="error" @retry="emit('retry')" />
      <template v-if="detail">
        <p v-if="loading" class="muted reading">读取中</p>
        <div v-if="mode === 'review' && !readonly" class="actions">
          <button
            type="button"
            class="act"
            :class="{ 'is-pending': pending === 'approve' }"
            :aria-busy="pending === 'approve'"
            :disabled="pending != null && pending !== 'approve'"
            @click="emit('approve')"
          >
            通过
          </button>
          <button
            type="button"
            class="act act--danger"
            :class="{ 'is-pending': pending === 'reject' }"
            :aria-busy="pending === 'reject'"
            :disabled="pending != null && pending !== 'reject'"
            @click="emit('reject')"
          >
            驳回
          </button>
        </div>
        <div v-else-if="mode === 'dispatch' && !readonly" class="actions">
          <button
            type="button"
            class="act"
            :class="{ 'is-pending': pending === 'dispatch' }"
            :aria-busy="pending === 'dispatch'"
            :disabled="pending != null && pending !== 'dispatch'"
            @click="emit('dispatch')"
          >
            派单
          </button>
          <button
            v-if="['assigned', 'running'].includes(detail.ticket.status)"
            type="button"
            class="act act--danger"
            :class="{ 'is-pending': pending === 'cancel' }"
            :aria-busy="pending === 'cancel'"
            :disabled="pending != null && pending !== 'cancel'"
            @click="emit('cancel')"
          >
            取消在途
          </button>
        </div>

        <section v-if="delivery" class="block delivery">
          <h3>交付</h3>
          <dl class="kv">
            <dt>分支</dt>
            <dd class="mono">{{ delivery.branch || "—" }}</dd>
            <dt>目标分支</dt>
            <dd class="mono">{{ prBase || "—" }}</dd>
            <dt>基线</dt>
            <dd class="mono">{{ delivery.base_commit || "—" }}</dd>
            <dt>推送</dt>
            <dd>{{ delivery.pushed ? "已推送" : "未推送" }}</dd>
            <dt>提交</dt>
            <dd><pre class="diff">{{ delivery.commits || "无" }}</pre></dd>
            <dt>变更</dt>
            <dd><pre class="diff">{{ delivery.diffstat || "无" }}</pre></dd>
          </dl>
          <div v-if="prUrl || testEnvUrl" class="links">
            <a v-if="prUrl" class="pr-link" :href="prUrl" target="_blank" rel="noreferrer">
              打开 PR ↗
            </a>
            <a
              v-if="testEnvUrl"
              class="pr-link pr-link--ghost"
              :href="testEnvUrl"
              target="_blank"
              rel="noreferrer"
            >
              测试环境 ↗
            </a>
          </div>
          <p v-else class="muted pr-hint">
            {{
              delivery.pushed
                ? "PR 尚未创建；控制面配置 GitHub 令牌后会自动建。"
                : "分支未推送，无法创建 PR。"
            }}
          </p>
        </section>

        <section class="block">
          <h3>基本信息</h3>
          <dl class="kv">
            <dt>编号</dt>
            <dd class="mono">{{ detail.ticket.code }}</dd>
            <dt>标题</dt>
            <dd>{{ detail.ticket.title }}</dd>
            <dt>状态</dt>
            <dd>
              <status-tag :status="detail.ticket.status" :label="statusText(detail.ticket.status)" />
            </dd>
            <dt>ID</dt>
            <dd class="mono">{{ detail.ticket.id }}</dd>
            <dt>Thread</dt>
            <dd class="mono">{{ textOrEmpty(detail.ticket.thread_id) }}</dd>
            <dt>去重键</dt>
            <dd class="mono">{{ textOrEmpty(detail.ticket.dedup_key) }}</dd>
            <dt>创建</dt>
            <dd>
              <time class="mono" :datetime="stampIso(detail.ticket.created_at)">
                {{ formatStamp(detail.ticket.created_at) }}
              </time>
            </dd>
            <dt>更新</dt>
            <dd>
              <time class="mono" :datetime="stampIso(detail.ticket.updated_at)">
                {{ formatStamp(detail.ticket.updated_at) }}
              </time>
            </dd>
          </dl>
        </section>

        <section class="block">
          <h3>来源 Thread</h3>
          <p v-if="!detail.thread" class="muted">无</p>
          <dl v-else class="kv">
            <dt>编号</dt>
            <dd class="mono">{{ detail.thread.id }}</dd>
            <dt>来源</dt>
            <dd>{{ textOrEmpty(detail.thread.origin) }}</dd>
            <dt>来源编号</dt>
            <dd class="mono">{{ textOrEmpty(detail.thread.origin_id) }}</dd>
            <dt>创建</dt>
            <dd>
              <time class="mono" :datetime="stampIso(detail.thread.created_at)">
                {{ formatStamp(detail.thread.created_at) }}
              </time>
            </dd>
          </dl>
        </section>

        <section class="block">
          <h3>Worker 分配</h3>
          <p v-if="detail.assignments.length === 0" class="muted">无</p>
          <div v-else class="scroll">
            <table class="dense">
              <thead>
                <tr>
                  <th>Worker</th>
                  <th>状态</th>
                  <th>租约至</th>
                  <th>更新</th>
                </tr>
              </thead>
              <tbody>
                <template v-for="item in detail.assignments" :key="item.worker_id">
                  <tr>
                    <td class="mono">{{ item.worker_id }}</td>
                    <td><status-tag :status="item.state" /></td>
                    <td class="mono">{{ formatStamp(item.lease_until) }}</td>
                    <td class="mono">{{ formatStamp(item.updated_at) }}</td>
                  </tr>
                  <tr v-if="item.result">
                    <td class="result" colspan="4">{{ item.result }}</td>
                  </tr>
                </template>
              </tbody>
            </table>
          </div>
        </section>

        <section class="block">
          <h3>派工记录</h3>
          <p v-if="detail.dispatches.length === 0" class="muted">无</p>
          <div v-else class="timeline">
            <article v-for="item in detail.dispatches" :key="item.id" class="step">
            <dl class="kv">
              <dt>编号</dt>
              <dd class="mono">{{ item.id }}</dd>
              <dt>节点</dt>
              <dd class="mono">{{ textOrEmpty(item.node_id) }}</dd>
              <dt>Worker</dt>
              <dd class="mono">{{ textOrEmpty(item.worker_id) }}</dd>
              <dt>状态</dt>
              <dd><status-tag :status="item.state" /></dd>
              <dt>尝试</dt>
              <dd class="mono">{{ item.attempt }}</dd>
              <dt>更新</dt>
              <dd>
                <time class="mono" :datetime="stampIso(item.updated_at)">
                  {{ formatStamp(item.updated_at) }}
                </time>
              </dd>
            </dl>
            <p class="detail-label">详情</p>
            <pre class="json">{{ formatJson(item.detail) }}</pre>
            </article>
          </div>
        </section>

        <section class="block">
          <h3>会话</h3>
          <p v-if="detail.sessions.length === 0" class="muted">无</p>
          <div v-else class="scroll">
            <table class="dense">
              <thead>
                <tr>
                  <th>会话</th>
                  <th>节点</th>
                  <th>轮次</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in detail.sessions" :key="item.id">
                  <td class="mono" :title="item.id">{{ item.id.slice(0, 8) }}</td>
                  <td class="mono">{{ textOrEmpty(item.node_id) }}</td>
                  <td class="mono">{{ item.turn_count }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </template>
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
:global(.n-drawer.ticket-drawer) {
  max-width: min(600px, 100vw) !important;
  border-radius: 0 !important;
  background-color: rgba(6, 9, 20, 0.96) !important;
  background-image: linear-gradient(270deg, rgba(10, 16, 36, 0.98), rgba(6, 9, 20, 0.94)) !important;
  box-shadow: -1px 0 0 rgba(79, 216, 235, 0.4), -28px 0 64px rgba(0, 0, 0, 0.62) !important;
}

:global(.ticket-drawer .n-drawer-header) {
  border-bottom: none !important;
  box-shadow: inset 0 -1px 0 rgba(79, 216, 235, 0.32);
  background: transparent;
  color: #fff;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 1px;
  text-shadow: 0 0 12px rgba(79, 216, 235, 0.4);
}

:global(.ticket-drawer .n-drawer-header__close),
:global(.ticket-drawer .n-base-close) {
  color: var(--muted);
  background: transparent !important;
}

:global(.ticket-drawer .n-drawer-header__close:hover),
:global(.ticket-drawer .n-base-close:hover) {
  color: var(--cyan) !important;
  background: transparent !important;
}

:global(.ticket-drawer .n-drawer-body-content-wrapper) {
  max-width: 100%;
  overflow-x: auto;
}

.state {
  padding: 4px 0 12px;
}

.reading {
  margin: 0 0 8px;
}

.actions {
  position: sticky;
  top: 0;
  z-index: 1;
  display: flex;
  flex-wrap: wrap;
  gap: 4px 8px;
  margin: 0 0 12px;
  padding-bottom: 8px;
  background: linear-gradient(270deg, rgba(10, 16, 36, 0.98), rgba(7, 10, 22, 0.96));
  box-shadow: inset 0 -1px 0 rgba(79, 216, 235, 0.22);
}

.act {
  position: relative;
  padding: 4px 8px 6px;
  border: none;
  background: none;
  color: var(--cyan);
  font: inherit;
  letter-spacing: 2px;
  text-shadow: 0 0 8px rgba(79, 216, 235, 0.45);
  cursor: pointer;
  transition: color 0.18s, text-shadow 0.18s;
}

.act::after {
  content: "";
  position: absolute;
  right: 10%;
  bottom: 1px;
  left: 10%;
  height: 1px;
  background: linear-gradient(90deg, transparent, currentColor, transparent);
  transform: scaleX(0);
  opacity: 0.85;
  transition: transform 0.22s ease;
  pointer-events: none;
}

.act:hover::after,
.act:focus-visible::after {
  transform: scaleX(1);
}

.act--danger {
  color: var(--bad);
  text-shadow: 0 0 8px rgba(242, 109, 125, 0.35);
}

.act:hover,
.act:focus-visible {
  color: #fff;
  text-shadow: 0 0 10px rgba(79, 216, 235, 0.85);
}

.act--danger:hover,
.act--danger:focus-visible {
  color: var(--bad);
  text-shadow: 0 0 10px rgba(242, 109, 125, 0.8);
}

.act:focus-visible {
  outline: 1px solid var(--cyan);
  outline-offset: 2px;
}

.act:disabled {
  opacity: 0.35;
  cursor: not-allowed;
  text-shadow: none;
}

.act:disabled::after {
  transform: scaleX(0);
}

.act.is-pending {
  opacity: 0.55;
  cursor: progress;
}

.block {
  min-width: 0;
  max-width: 100%;
  margin: 14px 0;
  padding: 8px 2px 8px 14px;
  border-left: 1px solid rgba(79, 216, 235, 0.35);
  box-shadow: -10px 0 16px -14px rgba(79, 216, 235, 0.85);
}

.block h3 {
  margin: 0 0 8px;
  color: var(--cyan);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 2px;
  text-shadow: 0 0 8px rgba(79, 216, 235, 0.45);
}

.kv {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 4px 10px;
  margin: 0;
  min-width: 0;
}

.kv dt {
  color: var(--cyan);
  font-size: 11px;
  letter-spacing: 0.12em;
  text-shadow: 0 0 8px rgba(79, 216, 235, 0.28);
}

.kv dd {
  margin: 0;
  min-width: 0;
  overflow-wrap: anywhere;
}

.mono {
  color: #d5def8;
  font-family: var(--mono);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.scroll {
  max-width: 100%;
  overflow-x: auto;
}

.dense {
  width: 100%;
  min-width: 0;
  border-collapse: collapse;
  table-layout: fixed;
}

.dense th,
.dense td {
  padding: 4px 6px;
  border-bottom: 1px solid rgba(125, 137, 184, 0.14);
  text-align: left;
  vertical-align: top;
  font-size: 12px;
  font-weight: 400;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.dense th {
  color: var(--muted);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.08em;
}

.dense th:nth-child(1),
.dense td:nth-child(1) {
  width: 34%;
}

.dense th:nth-child(2),
.dense td:nth-child(2) {
  width: 22%;
}

.dense th:nth-child(3),
.dense td:nth-child(3) {
  width: 22%;
}

.result {
  color: var(--text);
  white-space: pre-wrap;
}

.timeline {
  min-width: 0;
}

.step {
  position: relative;
  min-width: 0;
  padding: 2px 0 10px 14px;
  border-left: 1px solid rgba(125, 137, 184, 0.28);
}

.step::before {
  content: "";
  position: absolute;
  left: -3px;
  top: 12px;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--cyan);
  box-shadow: 0 0 6px var(--cyan);
}

.detail-label {
  margin: 8px 0 4px;
  color: var(--cyan);
  font-size: 11px;
  letter-spacing: 1px;
  text-shadow: 0 0 8px rgba(79, 216, 235, 0.28);
}

.json {
  margin: 0;
  padding: 8px 10px;
  max-width: 100%;
  max-height: 180px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: rgba(2, 4, 12, 0.6);
  border: none;
  box-shadow: inset 1px 0 0 rgba(79, 216, 235, 0.25);
  color: #a8b6e8;
  font-family: var(--mono);
  font-size: 12px;
  line-height: 1.45;
}

.diff {
  margin: 0;
  padding: 6px 8px;
  max-height: 140px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  background: rgba(2, 4, 12, 0.55);
  color: #a8b6e8;
  font-family: var(--mono);
  font-size: 12px;
  line-height: 1.4;
}

.links {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.pr-link {
  display: inline-block;
  padding: 5px 12px;
  color: #041018;
  background: var(--cyan);
  text-decoration: none;
  letter-spacing: 0.08em;
  transition: background 0.18s, box-shadow 0.18s;
}

.pr-link:hover,
.pr-link:focus-visible {
  background: #7ae6f4;
  box-shadow: 0 0 12px rgba(79, 216, 235, 0.45);
}

.pr-link--ghost {
  color: var(--cyan);
  background: transparent;
  box-shadow: inset 0 0 0 1px rgba(79, 216, 235, 0.55);
}

.pr-link--ghost:hover,
.pr-link--ghost:focus-visible {
  color: #041018;
  background: #7ae6f4;
}

.pr-hint {
  margin: 8px 0 0;
  font-size: 12px;
}
</style>
