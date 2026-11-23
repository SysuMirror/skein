<script setup lang="ts">
import { computed, h, ref, watch } from "vue";
import type { HTMLAttributes } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  NDataTable,
  NInput,
  NModal,
  NSkeleton,
  NTooltip,
  useDialog,
  useMessage,
  type DataTableColumns,
} from "naive-ui";
import { fetchTicket, fetchTickets, request, type Ticket, type TicketDetailResponse } from "../api";
import EmptyState from "../components/EmptyState.vue";
import ErrorBar from "../components/ErrorBar.vue";
import StatusTag from "../components/StatusTag.vue";
import TicketDetailDrawer from "../components/TicketDetailDrawer.vue";
import { useConsoleStore } from "../store";

type ActionName = "approve" | "reject" | "dispatch" | "cancel";

const FILTERS: { value: string; label: string }[] = [
  { value: "", label: "全部" },
  { value: "staged", label: "暂存" },
  { value: "assigned", label: "已派发" },
  { value: "running", label: "进行中" },
  { value: "review", label: "待验收" },
  { value: "done", label: "完成" },
  { value: "rejected", label: "已驳回" },
];

const STATUS_LABEL: Record<string, string> = {
  staged: "暂存",
  assigned: "已派发",
  running: "进行中",
  review: "待验收",
  done: "完成",
  rejected: "已驳回",
};

const TABLE_MIN_WIDTH = 980;

const dialog = useDialog();
const message = useMessage();
const route = useRoute();
const router = useRouter();
const store = useConsoleStore();

// 能不能派单 / 验收 / 取消由权限矩阵决定：成员只读，控件直接不出现
const canOperate = computed(() => store.can("ticket.operate"));

// 派单要带平台。派过一次就记住，同一张单不再问
const platformMemory = ref<Record<string, string>>({});

// 建单：能看控制台的人都能提一张（派工仍然要管理员）
const createOpen = ref(false);
const createTitle = ref("");
const createSummary = ref("");
const creating = ref(false);
const picker = ref<{
  id: string;
  code: string;
  platforms: string[];
  resolve: (platform: string | null) => void;
} | null>(null);

interface DispatchResult {
  ok: boolean;
  reason?: string;
  platforms?: string[];
  dispatch_id?: string;
  state?: string;
  platform?: string;
}

const DISPATCH_ERRORS: Record<string, string> = {
  no_node: "没有在线节点能接这个平台",
  node_not_connected: "节点刚才掉线了，稍后重试",
  already_dispatched: "这张单已经有在途派工",
  ticket_not_found: "工单不存在（可能已被删除）",
};

function askPlatform(id: string, platforms: string[]): Promise<string | null> {
  return new Promise((resolve) => {
    picker.value = { id, code: codeOf(id), platforms, resolve };
  });
}

function pickPlatform(platform: string | null): void {
  const current = picker.value;
  picker.value = null;
  current?.resolve(platform);
}

const statusFilter = ref("");
const tickets = ref<Ticket[]>([]);
const loading = ref(true);
const refreshing = ref(false);
const error = ref<string | null>(null);
const acting = ref<Partial<Record<string, ActionName>>>({});

const drawerOpen = ref(false);
const selectedId = ref<string | null>(null);
const detail = ref<TicketDetailResponse | null>(null);
const detailLoading = ref(false);
const detailError = ref<string | null>(null);

let listSeq = 0;
let detailSeq = 0;
let confirmOpen = false;

const countText = computed(() => (loading.value ? "读取中" : `${tickets.value.length} 条`));

const pendingAction = computed<ActionName | null>(() => {
  const id = selectedId.value;
  if (!id) return null;
  return acting.value[id] ?? null;
});

const columns = computed<DataTableColumns<Ticket>>(() => {
  const pendingMap = acting.value;
  return [
    {
      title: "编号",
      key: "code",
      width: 148,
      render(row) {
        return h("span", { class: "mono code" }, row.code);
      },
    },
    {
      title: "标题",
      key: "title",
      minWidth: 220,
      className: "col-title",
      render(row) {
        return h(
          NTooltip,
          {
            placement: "top-start",
            width: 420,
            contentStyle: { whiteSpace: "pre-wrap", wordBreak: "break-word" },
          },
          {
            trigger: () => h("div", { class: "title-clamp" }, row.title),
            default: () => row.title,
          },
        );
      },
    },
    {
      title: "状态",
      key: "status",
      width: 108,
      render(row) {
        return h(StatusTag, { status: row.status, label: statusText(row.status) });
      },
    },
    {
      title: "来源话题",
      key: "thread_id",
      width: 130,
      render(row) {
        if (!row.thread_id) return h("span", { class: "muted" }, "—");
        const text = row.thread_id;
        return h(
          NTooltip,
          { placement: "top-start" },
          {
            trigger: () =>
              h("span", { class: "mono clip", title: text }, `#${text.slice(0, 8)}`),
            default: () => h("span", { class: "mono" }, text),
          },
        );
      },
    },
    {
      title: "更新时间",
      key: "updated_at",
      width: 112,
      render(row) {
        return h(
          "time",
          { class: "mono", datetime: stampIso(row.updated_at) },
          formatStamp(row.updated_at),
        );
      },
    },
    {
      title: "操作",
      key: "actions",
      width: 156,
      className: "col-actions",
      render(row) {
        return renderActions(row, pendingMap[row.id] ?? null);
      },
    },
  ];
});

function statusText(status: string): string {
  return STATUS_LABEL[status] ?? status;
}

function actionMode(status: string): "review" | "dispatch" | "none" {
  if (status === "review") return "review";
  if (status === "done" || status === "rejected") return "none";
  return "dispatch";
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

function errorText(err: unknown): string {
  if (err instanceof Error && err.message) return err.message;
  return "请求失败";
}

function codeOf(id: string): string {
  const listed = tickets.value.find((item) => item.id === id)?.code;
  if (listed) return listed;
  if (detail.value?.ticket.id === id) return detail.value.ticket.code;
  return id;
}

async function loadList(kind: "load" | "refresh"): Promise<void> {
  const seq = ++listSeq;
  if (kind === "load") loading.value = true;
  else refreshing.value = true;
  error.value = null;
  try {
    const result = await fetchTickets({
      status: statusFilter.value || undefined,
      limit: 100,
    });
    if (seq !== listSeq) return;
    if (!Array.isArray(result.tickets)) throw new Error("工单列表格式错误");
    tickets.value = result.tickets;
  } catch (err) {
    if (seq !== listSeq) return;
    error.value = errorText(err);
    if (kind === "load") tickets.value = [];
  } finally {
    if (seq === listSeq) {
      loading.value = false;
      refreshing.value = false;
    }
  }
}

async function loadDetail(id: string, replace: boolean): Promise<void> {
  const seq = ++detailSeq;
  detailLoading.value = true;
  detailError.value = null;
  try {
    const body = await fetchTicket(id);
    if (seq !== detailSeq || selectedId.value !== id) return;
    if (!body?.ticket) throw new Error("工单详情格式错误");
    detail.value = body;
  } catch (err) {
    if (seq !== detailSeq || selectedId.value !== id) return;
    detailError.value = errorText(err);
    if (replace) detail.value = null;
  } finally {
    if (seq === detailSeq) detailLoading.value = false;
  }
}

function openTicket(row: Ticket): void {
  if (route.params.id !== row.id) {
    void router.push(`/tickets/${encodeURIComponent(row.id)}`);
  }
  const same = selectedId.value === row.id && detail.value?.ticket.id === row.id;
  selectedId.value = row.id;
  drawerOpen.value = true;
  if (!same) {
    detail.value = null;
    detailError.value = null;
  }
  void loadDetail(row.id, !same);
}

function onDrawerShow(value: boolean): void {
  if (!value) void router.replace("/tickets");
}

function syncFromRoute(): void {
  const raw = route.params.id;
  const id = typeof raw === "string" ? raw : "";
  if (!id) {
    if (drawerOpen.value) drawerOpen.value = false;
    if (selectedId.value !== null) selectedId.value = null;
    return;
  }
  const same = selectedId.value === id && detail.value?.ticket.id === id;
  selectedId.value = id;
  drawerOpen.value = true;
  if (!same) {
    detail.value = null;
    detailError.value = null;
  }
  void loadDetail(id, !same);
}

function retryDetail(): void {
  if (!selectedId.value) return;
  void loadDetail(selectedId.value, detail.value == null);
}

async function refreshAfter(id: string): Promise<void> {
  await loadList("refresh");
  if (!drawerOpen.value || selectedId.value !== id) return;
  await loadDetail(id, false);
}

async function submitCreate(): Promise<void> {
  const title = createTitle.value.trim();
  if (!title || creating.value) {
    if (!title) message.warning("请填写标题");
    return;
  }
  creating.value = true;
  try {
    await request("/internal/tickets", {
      method: "POST",
      body: JSON.stringify({ title, summary: createSummary.value.trim() }),
    });
    message.success("已建单，可以在列表里派单");
    createOpen.value = false;
    createTitle.value = "";
    createSummary.value = "";
    await loadList("refresh");
  } catch (err) {
    message.error(errorText(err));
  } finally {
    creating.value = false;
  }
}

async function dispatchTicket(id: string): Promise<void> {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const platform = platformMemory.value[id];
    const query = platform ? `?platform=${encodeURIComponent(platform)}` : "";
    const result = (await request<DispatchResult>(
      `/internal/tickets/${encodeURIComponent(id)}/dispatch${query}`,
      { method: "POST" },
    )) ?? { ok: true };
    if (result.ok) return;
    if (result.reason !== "platform_required") {
      throw new Error(DISPATCH_ERRORS[result.reason ?? ""] ?? result.reason ?? "派单失败");
    }
    const platforms = result.platforms ?? [];
    const picked = await askPlatform(id, platforms);
    if (!picked) return; // 操作者点了取消
    platformMemory.value = { ...platformMemory.value, [id]: picked };
  }
}

async function runAction(id: string, action: ActionName): Promise<void> {
  if (acting.value[id]) return;
  acting.value = { ...acting.value, [id]: action };
  try {
    if (action === "dispatch") {
      await dispatchTicket(id);
    } else if (action === "cancel") {
      await request(`/internal/tickets/${encodeURIComponent(id)}/cancel`, { method: "POST" });
    } else {
      await request(`/internal/tickets/${encodeURIComponent(id)}/review`, {
        method: "POST",
        body: JSON.stringify({ decision: action }),
      });
    }
    await refreshAfter(id);
  } catch (err) {
    message.error(errorText(err));
  } finally {
    if (acting.value[id] === action) {
      const next = { ...acting.value };
      delete next[id];
      acting.value = next;
    }
  }
}

function confirmReject(id: string): void {
  if (confirmOpen || acting.value[id]) return;
  confirmOpen = true;
  dialog.warning({
    title: "驳回工单",
    content: `确认驳回 ${codeOf(id)}？状态将变为已驳回。`,
    positiveText: "驳回",
    negativeText: "取消",
    showIcon: false,
    positiveButtonProps: { type: "error" },
    onPositiveClick: () => runAction(id, "reject"),
    onAfterLeave: () => {
      confirmOpen = false;
    },
  });
}

function confirmDispatch(id: string): void {
  if (confirmOpen || acting.value[id]) return;
  confirmOpen = true;
  dialog.warning({
    title: "派单",
    content: `确认派发 ${codeOf(id)}？`,
    positiveText: "派单",
    negativeText: "取消",
    showIcon: false,
    onPositiveClick: () => runAction(id, "dispatch"),
    onAfterLeave: () => {
      confirmOpen = false;
    },
  });
}

function renderActions(row: Ticket, pending: ActionName | null) {
  if (!canOperate.value) return h("span", { class: "muted" }, "只读");
  const mode = actionMode(row.status);
  if (mode === "none") return h("span", { class: "muted" }, "—");
  const button = (label: string, action: ActionName, onClick: () => void) =>
    h(
      "button",
      {
        type: "button",
        class: [
          "act",
          action === "reject" ? "act--danger" : null,
          pending === action ? "is-pending" : null,
        ],
        disabled: pending != null && pending !== action,
        "aria-busy": pending === action,
        onClick: (event: MouseEvent) => {
          event.stopPropagation();
          onClick();
        },
      },
      label,
    );
  if (mode === "review") {
    return h("div", { class: "row-actions" }, [
      button("通过", "approve", () => {
        void runAction(row.id, "approve");
      }),
      button("驳回", "reject", () => confirmReject(row.id)),
    ]);
  }
  return h("div", { class: "row-actions" }, [
    button("派单", "dispatch", () => confirmDispatch(row.id)),
  ]);
}

function rowProps(row: Ticket): HTMLAttributes {
  return {
    onClick: (event: MouseEvent) => {
      const target = event.target;
      if (target instanceof Element && target.closest("td.col-actions")) return;
      openTicket(row);
    },
  };
}

function rowClassName(row: Ticket): string {
  return drawerOpen.value && row.id === selectedId.value ? "is-selected" : "";
}

function applyFilter(value: string): void {
  if (statusFilter.value === value) return;
  statusFilter.value = value;
  void loadList("load");
}

function retryList(): void {
  void loadList(tickets.value.length > 0 ? "refresh" : "load");
}

function refresh(): void {
  void loadList(loading.value ? "load" : "refresh");
}

function onApprove(): void {
  if (!selectedId.value) return;
  void runAction(selectedId.value, "approve");
}

function onReject(): void {
  if (!selectedId.value) return;
  confirmReject(selectedId.value);
}

function onDispatch(): void {
  if (!selectedId.value) return;
  confirmDispatch(selectedId.value);
}

function onCancel(): void {
  if (!selectedId.value) return;
  const id = selectedId.value;
  dialog.warning({
    title: "取消在途",
    content: `确认取消 ${codeOf(id)} 的在途派工？工单会回到暂存。`,
    positiveText: "取消在途",
    negativeText: "返回",
    showIcon: false,
    positiveButtonProps: { type: "error" },
    onPositiveClick: () => runAction(id, "cancel"),
  });
}

watch(() => route.params.id, syncFromRoute, { immediate: true });
void loadList("load");
</script>

<template>
  <section class="tickets">
    <header class="head">
      <h2 class="page__title">工单</h2>
      <div class="head__side">
        <span class="muted count">{{ countText }}</span>
        <button type="button" class="refresh" :aria-busy="loading || refreshing" @click="refresh">
          刷新
        </button>
        <button type="button" class="refresh refresh--primary" @click="createOpen = true">
          建单
        </button>
      </div>
    </header>
    <div class="filters" role="group" aria-label="状态筛选">
      <button
        v-for="item in FILTERS"
        :key="item.value || 'all'"
        type="button"
        class="filter"
        :class="{ on: statusFilter === item.value }"
        :aria-pressed="statusFilter === item.value"
        @click="applyFilter(item.value)"
      >
        {{ item.label }}
      </button>
    </div>
    <error-bar v-if="error" :message="error" @retry="retryList" />
    <div v-if="loading" class="fill" aria-busy="true">
      <n-skeleton text :repeat="8" :animated="false" />
    </div>
    <div v-else-if="tickets.length > 0 || !error" class="table-wrap">
      <n-data-table
        flex-height
        size="small"
        :columns="columns"
        :data="tickets"
        :bordered="false"
        :single-line="true"
        :bottom-bordered="false"
        :scroll-x="TABLE_MIN_WIDTH"
        :row-key="(row: Ticket) => row.id"
        :row-props="rowProps"
        :row-class-name="rowClassName"
      >
        <template #empty>
          <empty-state title="暂无工单" />
        </template>
      </n-data-table>
    </div>
    <ticket-detail-drawer
      v-model:show="drawerOpen"
      @update:show="onDrawerShow"
      :loading="detailLoading"
      :error="detailError"
      :detail="detail"
      :pending="pendingAction"
      :readonly="!canOperate"
      @retry="retryDetail"
      @approve="onApprove"
      @reject="onReject"
      @dispatch="onDispatch"
      @cancel="onCancel"
    />

    <n-modal
      v-model:show="createOpen"
      preset="card"
      title="新建工单"
      :style="{ width: '480px' }"
      class="dialog-card"
    >
      <div class="create-form">
        <label class="create-form__label">
          标题
          <n-input v-model:value="createTitle" placeholder="一句话说清是什么问题" size="small" />
        </label>
        <label class="create-form__label">
          说明（可选）
          <n-input
            v-model:value="createSummary"
            type="textarea"
            :autosize="{ minRows: 4, maxRows: 10 }"
            placeholder="复现步骤、期望结果、相关链接；粘贴进来的内容会原样进入工单"
          />
        </label>
      </div>
      <template #footer>
        <button type="button" class="ghost" @click="createOpen = false">取消</button>
        <button type="button" class="primary" :aria-busy="creating" @click="submitCreate">建单</button>
      </template>
    </n-modal>

    <div v-if="picker" class="picker" role="dialog" aria-modal="true" aria-labelledby="picker-title">
      <div class="picker__panel">
        <h3 id="picker-title">把 {{ picker.code }} 派到哪个平台？</h3>
        <div v-if="picker.platforms.length" class="picker__options">
          <button
            v-for="name in picker.platforms"
            :key="name"
            type="button"
            class="picker__option mono"
            @click="pickPlatform(name)"
          >
            {{ name }}
          </button>
        </div>
        <p v-else class="muted">现在没有在线节点登记任何平台。</p>
        <button type="button" class="picker__cancel" @click="pickPlatform(null)">取消</button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.refresh--primary {
  border-color: #4fd8eb;
  color: #4fd8eb;
}

.create-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.create-form__label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: #7d89b8;
}

.ghost,
.primary {
  padding: 5px 14px;
  border: 1px solid #303a54;
  background: transparent;
  color: #dfe6ff;
  font-size: 12px;
  letter-spacing: 0.06em;
  cursor: pointer;
}

.ghost:hover {
  border-color: #4fd8eb;
  color: #4fd8eb;
}

.primary {
  border-color: #4fd8eb;
  background: #4fd8eb;
  color: #041018;
}

.primary:hover {
  background: #7ae6f4;
}

.picker {
  position: fixed;
  inset: 0;
  z-index: 40;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(4, 6, 14, 0.72);
}

.picker__panel {
  width: min(420px, 90vw);
  padding: 20px 22px;
  border: 1px solid #303a54;
  background: #0a1024;
}

.picker__panel h3 {
  margin: 0 0 14px;
  font-size: 14px;
  font-weight: 500;
  letter-spacing: 0.04em;
}

.picker__options {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.picker__option,
.picker__cancel {
  padding: 6px 14px;
  border: 1px solid #303a54;
  background: transparent;
  color: var(--text, #dfe6ff);
  font-size: 12px;
  letter-spacing: 0.06em;
  cursor: pointer;
}

.picker__option:hover,
.picker__cancel:hover {
  border-color: #4fd8eb;
  color: #4fd8eb;
}

.picker__cancel {
  margin-top: 16px;
}

.tickets {
  height: 100%;
  min-width: 0;
  max-width: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.head {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
  margin-bottom: 8px;
}

.head .page__title {
  margin: 0;
  min-width: 0;
}

.head__side {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 10px;
}

.count {
  font-family: var(--mono);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.refresh,
.filter {
  position: relative;
  border: none;
  background: none;
  font: inherit;
  cursor: pointer;
  transition: color 0.18s, text-shadow 0.18s;
}

.refresh::after,
.filter::after {
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

.refresh:hover::after,
.refresh:focus-visible::after,
.filter:hover::after,
.filter:focus-visible::after,
.filter.on::after {
  transform: scaleX(1);
}

.refresh {
  flex: 0 0 auto;
  padding: 2px 4px 5px;
  color: var(--text);
  letter-spacing: 2px;
}

.refresh:hover,
.refresh:focus-visible {
  color: var(--cyan);
  text-shadow: 0 0 10px rgba(79, 216, 235, 0.75);
}

.refresh[aria-busy="true"] {
  opacity: 0.55;
}

.filters {
  flex: 0 0 auto;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 2px 4px;
  min-width: 0;
  max-width: 100%;
  margin-bottom: 8px;
}

.filter {
  padding: 4px 8px 6px;
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 1px;
}

.filter:hover,
.filter:focus-visible,
.filter.on {
  color: var(--cyan);
  text-shadow: 0 0 8px rgba(79, 216, 235, 0.7);
}

.refresh:focus-visible,
.filter:focus-visible {
  outline: 1px solid var(--cyan);
  outline-offset: 2px;
}

.fill {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow: auto;
}

.table-wrap {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  max-width: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.table-wrap :deep(.n-data-table) {
  --n-th-color: transparent !important;
  --n-td-color: transparent !important;
  --n-th-color-hover: transparent !important;
  --n-td-color-hover: transparent !important;
  --n-border-color: rgba(79, 216, 235, 0.14) !important;
  --n-th-text-color: var(--muted) !important;
  --n-merged-th-color: transparent !important;
  --n-merged-td-color: transparent !important;
  --n-merged-td-color-hover: transparent !important;
  --n-merged-border-color: rgba(79, 216, 235, 0.14) !important;
  flex: 1 1 auto;
  min-height: 0;
  height: 100%;
  background: transparent !important;
  font-size: 12px;
}

.table-wrap :deep(.n-data-table-wrapper),
.table-wrap :deep(.n-data-table-base-table),
.table-wrap :deep(.n-data-table-table) {
  background: transparent !important;
  border: none !important;
}

.table-wrap :deep(.n-data-table-thead),
.table-wrap :deep(.n-data-table-base-table-header) {
  background: rgba(4, 6, 14, 0.94) !important;
  border: none !important;
}

.table-wrap :deep(.n-data-table-th),
.table-wrap :deep(.n-data-table-td) {
  padding: 7px 8px;
  background-color: transparent !important;
  border-right: none !important;
  border-bottom: 1px solid rgba(125, 137, 184, 0.14) !important;
}

.table-wrap :deep(.n-data-table-th) {
  color: var(--muted) !important;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.12em;
  background-color: rgba(4, 6, 14, 0.94) !important;
  border-bottom-color: rgba(79, 216, 235, 0.22) !important;
}

.table-wrap :deep(.n-data-table-tr) {
  cursor: pointer;
  background-color: transparent !important;
  transition: background-image 0.15s ease;
}

.table-wrap :deep(.n-data-table-tr:hover) {
  background-color: transparent !important;
  background-image: linear-gradient(90deg, rgba(79, 216, 235, 0.1), transparent 68%);
}

.table-wrap :deep(.n-data-table-tr:hover > .n-data-table-td) {
  background-color: transparent !important;
  background-image: none !important;
}

.table-wrap :deep(.n-data-table-tr:hover > .n-data-table-td:first-child) {
  box-shadow: inset 2px 0 0 rgba(79, 216, 235, 0.7);
}

.table-wrap :deep(.n-data-table-tr.is-selected) {
  background-color: transparent !important;
  background-image: linear-gradient(90deg, rgba(79, 216, 235, 0.12), transparent 40%);
}

.table-wrap :deep(.n-data-table-tr.is-selected > .n-data-table-td) {
  background-color: transparent !important;
  background-image: none !important;
}

.table-wrap :deep(.n-data-table-tr.is-selected > .n-data-table-td:first-child),
.table-wrap :deep(.n-data-table-tr.is-selected:hover > .n-data-table-td:first-child) {
  box-shadow: inset 2px 0 0 var(--cyan), inset 14px 0 16px -12px rgba(79, 216, 235, 0.85);
}

.table-wrap :deep(.n-data-table-tr:hover .title-clamp) {
  color: #fff;
  text-shadow: 0 0 8px rgba(79, 216, 235, 0.4);
}

.table-wrap :deep(.mono),
.table-wrap :deep(.code) {
  font-family: var(--mono);
  font-variant-numeric: tabular-nums;
}

.table-wrap :deep(.code) {
  display: inline;
  color: var(--cyan);
  text-shadow: 0 0 8px rgba(79, 216, 235, 0.35);
  white-space: normal;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.table-wrap :deep(.clip) {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.table-wrap :deep(td.col-clip) {
  max-width: 0;
}

.table-wrap :deep(td.col-title) {
  max-width: 0;
  white-space: normal;
}

.table-wrap :deep(.title-clamp) {
  display: -webkit-box;
  overflow: hidden;
  max-width: 100%;
  white-space: normal;
  word-break: break-word;
  line-height: 1.4;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  line-clamp: 2;
}

.table-wrap :deep(td.col-actions) {
  white-space: nowrap;
}

.table-wrap :deep(.row-actions) {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}

.table-wrap :deep(.act) {
  position: relative;
  padding: 2px 6px 4px;
  border: none;
  background: none;
  color: var(--cyan);
  font: inherit;
  font-size: 12px;
  letter-spacing: 1px;
  text-shadow: 0 0 8px rgba(79, 216, 235, 0.4);
  cursor: pointer;
  transition: color 0.18s, text-shadow 0.18s;
}

.table-wrap :deep(.act)::after {
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

.table-wrap :deep(.act:hover)::after,
.table-wrap :deep(.act:focus-visible)::after {
  transform: scaleX(1);
}

.table-wrap :deep(.act--danger) {
  color: var(--bad);
  text-shadow: 0 0 8px rgba(242, 109, 125, 0.35);
}

.table-wrap :deep(.act:hover),
.table-wrap :deep(.act:focus-visible) {
  color: #fff;
  text-shadow: 0 0 10px rgba(79, 216, 235, 0.8);
}

.table-wrap :deep(.act--danger:hover),
.table-wrap :deep(.act--danger:focus-visible) {
  color: var(--bad);
  text-shadow: 0 0 10px rgba(242, 109, 125, 0.8);
}

.table-wrap :deep(.act:focus-visible) {
  outline: 1px solid var(--cyan);
  outline-offset: 2px;
}

.table-wrap :deep(.act:disabled) {
  opacity: 0.35;
  cursor: not-allowed;
  text-shadow: none;
}

.table-wrap :deep(.act:disabled)::after {
  transform: scaleX(0);
}

.table-wrap :deep(.act.is-pending) {
  opacity: 0.55;
  cursor: progress;
}
</style>
