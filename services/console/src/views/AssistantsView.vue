<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { NInput, NModal, useDialog, useMessage } from "naive-ui";
import {
  fetchAssistants,
  issueAssistant,
  revokeAssistant,
  rotateAssistant,
  updateAssistant,
  type AssistantRecord,
  type IssuedAssistant,
} from "../api";
import EmptyState from "../components/EmptyState.vue";
import ErrorBar from "../components/ErrorBar.vue";

const dialog = useDialog();
const message = useMessage();

const items = ref<AssistantRecord[]>([]);
const loading = ref(true);
const error = ref<string | null>(null);
const busy = ref<string | null>(null);

// 签发 / 轮换 / 改回调的结果：密钥与签名密钥只在这里出现一次
const issued = ref<{ title: string; data: IssuedAssistant } | null>(null);

const issueOpen = ref(false);
const issueName = ref("");
const issuePurpose = ref("");
const issueCallback = ref("");
const issuing = ref(false);

const callbackOpen = ref(false);
const callbackFor = ref<AssistantRecord | null>(null);
const callbackUrl = ref("");
const callbackRekey = ref(false);
const callbackBusy = ref(false);

const active = computed(() => items.value.filter((item) => item.revoked_at == null));
const revoked = computed(() => items.value.filter((item) => item.revoked_at != null));

function errorText(err: unknown): string {
  if (err instanceof Error && err.message) return err.message;
  return "操作失败";
}

async function load(): Promise<void> {
  error.value = null;
  try {
    const body = await fetchAssistants();
    items.value = body.assistants;
  } catch (err) {
    error.value = errorText(err);
  } finally {
    loading.value = false;
  }
}

function formatStamp(ts: number | null | undefined): string {
  if (!ts) return "—";
  const date = new Date(ts * 1000);
  if (Number.isNaN(date.getTime())) return "—";
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

async function copy(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    message.success("已复制");
  } catch {
    message.warning("浏览器不允许自动复制，请手动选中复制");
  }
}

async function submitIssue(): Promise<void> {
  if (issuing.value) return;
  const name = issueName.value.trim().toLowerCase();
  if (!name) {
    message.warning("请填写名字");
    return;
  }
  issuing.value = true;
  try {
    const result = await issueAssistant({
      name,
      purpose: issuePurpose.value.trim(),
      callback_url: issueCallback.value.trim(),
    });
    issued.value = { title: `已签发 ${result.assistant.name}`, data: result };
    issueOpen.value = false;
    issueName.value = "";
    issuePurpose.value = "";
    issueCallback.value = "";
    await load();
  } catch (err) {
    message.error(errorText(err));
  } finally {
    issuing.value = false;
  }
}

function confirmRotate(item: AssistantRecord): void {
  if (busy.value) return;
  dialog.warning({
    title: "轮换密钥",
    content: `给 ${item.name} 换一把新密钥？旧密钥会立刻失效，请记得把新密钥更新到对方的环境里。`,
    positiveText: "轮换",
    negativeText: "取消",
    showIcon: false,
    async onPositiveClick() {
      busy.value = item.id;
      try {
        const result = await rotateAssistant(item.id);
        issued.value = { title: `已轮换 ${item.name}`, data: result };
        await load();
      } catch (err) {
        message.error(errorText(err));
      } finally {
        busy.value = null;
      }
    },
  });
}

function confirmRevoke(item: AssistantRecord): void {
  if (busy.value) return;
  dialog.warning({
    title: "吊销凭证",
    content: `吊销 ${item.name}？对方立刻无法再提交请求，记录会保留。`,
    positiveText: "吊销",
    negativeText: "取消",
    showIcon: false,
    positiveButtonProps: { type: "error" },
    async onPositiveClick() {
      busy.value = item.id;
      try {
        await revokeAssistant(item.id);
        message.success(`已吊销 ${item.name}`);
        await load();
      } catch (err) {
        message.error(errorText(err));
      } finally {
        busy.value = null;
      }
    },
  });
}

function openCallback(item: AssistantRecord): void {
  callbackFor.value = item;
  callbackUrl.value = item.callback_url;
  callbackRekey.value = false;
  callbackOpen.value = true;
}

async function submitCallback(): Promise<void> {
  const item = callbackFor.value;
  if (!item || callbackBusy.value) return;
  callbackBusy.value = true;
  try {
    const result = await updateAssistant(item.id, {
      callback_url: callbackUrl.value.trim(),
      new_callback_secret: callbackRekey.value,
    });
    if (result.callback_secret) {
      issued.value = { title: `已生成 ${item.name} 的回调签名密钥`, data: result };
    } else {
      message.success("已更新回调地址");
    }
    callbackOpen.value = false;
    await load();
  } catch (err) {
    message.error(errorText(err));
  } finally {
    callbackBusy.value = false;
  }
}

const curlExample = computed(() => {
  const base = `${window.location.origin}`;
  return `curl -s -X POST ${base}/api/v1/tickets \\
  -H "Authorization: Bearer $SKEIN_ASSISTANT_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"title":"登录页在手机上错位","source":"forum","external_id":"post:42","requester":"forum:278"}'`;
});

onMounted(() => {
  void load();
});
</script>

<template>
  <section class="assistants">
    <header class="head">
      <div>
        <h2 class="page__title">助手</h2>
        <p class="muted">
          外部助手（论坛机器人、聊天助手、巡检脚本）用各自的凭证向雁阵提交请求；
          它们只能建单和查自己的工单，派工与验收仍然是人来做。
        </p>
      </div>
      <div class="head__side">
        <button type="button" class="ghost" :aria-busy="loading" @click="load">刷新</button>
        <button type="button" class="primary" @click="issueOpen = true">签发凭证</button>
      </div>
    </header>

    <error-bar v-if="error" :message="error" @retry="load" />

    <section class="panel">
      <h3 class="panel__title">接入示例</h3>
      <pre class="snippet mono">{{ curlExample }}</pre>
      <p class="muted">
        把 <span class="mono">SKEIN_ASSISTANT_KEY</span> 换成签发时拿到的那把
        <span class="mono">skein_at_…</span>。同一把凭证下
        <span class="mono">(source, external_id)</span> 是幂等键：失败重试不会重复建单。
        完整说明见仓库 <span class="mono">docs/assistants.md</span>。
      </p>
    </section>

    <section class="panel">
      <h3 class="panel__title">在用（{{ active.length }}）</h3>
      <div v-if="active.length" class="list">
        <article v-for="item in active" :key="item.id" class="row">
          <div class="row__main">
            <span class="mono row__name">{{ item.name }}</span>
            <span v-if="item.source === 'env'" class="tag">环境文件声明</span>
            <span v-if="item.callback_url" class="tag">回调</span>
            <p class="muted row__purpose">{{ item.purpose || "（没有填写用途）" }}</p>
            <p class="muted row__meta">
              最近使用 {{ formatStamp(item.last_used_at) }} · 签发于
              {{ formatStamp(item.created_at) }}
            </p>
          </div>
          <div class="row__actions">
            <button type="button" class="mini" :disabled="busy === item.id" @click="openCallback(item)">
              回调
            </button>
            <button type="button" class="mini" :disabled="busy === item.id" @click="confirmRotate(item)">
              轮换
            </button>
            <button
              type="button"
              class="mini danger"
              :disabled="busy === item.id"
              @click="confirmRevoke(item)"
            >
              吊销
            </button>
          </div>
        </article>
      </div>
      <empty-state
        v-else-if="!loading"
        title="还没有助手凭证"
        description="签发一把，按上面的示例接上你的助手；密钥只显示一次。"
      />
    </section>

    <section v-if="revoked.length" class="panel">
      <h3 class="panel__title">已吊销（{{ revoked.length }}）</h3>
      <div class="list">
        <article v-for="item in revoked" :key="item.id" class="row row--muted">
          <div class="row__main">
            <span class="mono row__name">{{ item.name }}</span>
            <p class="muted row__meta">吊销于 {{ formatStamp(item.revoked_at) }}</p>
          </div>
        </article>
      </div>
    </section>

    <n-modal
      v-model:show="issueOpen"
      preset="card"
      title="签发助手凭证"
      class="dialog-card"
      :style="{ width: '480px' }"
    >
      <div class="form">
        <label class="form__label">
          名字（小写字母、数字与连字符）
          <n-input v-model:value="issueName" placeholder="forum-bot" size="small" />
        </label>
        <label class="form__label">
          用途（给人看的）
          <n-input v-model:value="issuePurpose" placeholder="论坛前厅：把帖子里的 bug 变成工单" size="small" />
        </label>
        <label class="form__label">
          状态回调地址（可选）
          <n-input v-model:value="issueCallback" placeholder="https://bot.example.com/internal/skein/notify" size="small" />
        </label>
      </div>
      <template #footer>
        <button type="button" class="ghost" @click="issueOpen = false">取消</button>
        <button type="button" class="primary" :aria-busy="issuing" @click="submitIssue">签发</button>
      </template>
    </n-modal>

    <n-modal
      v-model:show="callbackOpen"
      preset="card"
      title="状态回调"
      class="dialog-card"
      :style="{ width: '480px' }"
    >
      <div class="form">
        <label class="form__label">
          回调地址（留空 = 不发回调）
          <n-input v-model:value="callbackUrl" placeholder="https://bot.example.com/internal/skein/notify" size="small" />
        </label>
        <label class="form__check">
          <input v-model="callbackRekey" type="checkbox" />
          重新生成签名密钥
        </label>
        <p class="muted">
          雁阵会带着 <span class="mono">X-Skein-Webhook-Signature</span> 头把工单状态 POST 到这个地址；
          签名密钥只在这里出现一次。
        </p>
      </div>
      <template #footer>
        <button type="button" class="ghost" @click="callbackOpen = false">取消</button>
        <button type="button" class="primary" :aria-busy="callbackBusy" @click="submitCallback">保存</button>
      </template>
    </n-modal>

    <n-modal
      :show="issued != null"
      preset="card"
      :title="issued?.title ?? ''"
      class="dialog-card"
      :style="{ width: '560px' }"
      @update:show="issued = null"
    >
      <template v-if="issued">
        <template v-if="issued.data.key">
          <p class="muted">密钥只显示这一次，请现在就填进对方的配置里。</p>
          <div class="secret">
            <code class="mono secret__value">{{ issued.data.key }}</code>
            <button type="button" class="mini" @click="copy(issued.data.key ?? '')">复制</button>
          </div>
        </template>
        <template v-if="issued.data.callback_secret">
          <p class="muted">状态回调的签名密钥（用于校验回调确实来自雁阵）：</p>
          <div class="secret">
            <code class="mono secret__value">{{ issued.data.callback_secret }}</code>
            <button type="button" class="mini" @click="copy(issued.data.callback_secret ?? '')">复制</button>
          </div>
        </template>
      </template>
      <template #footer>
        <button type="button" class="primary" @click="issued = null">我已保存</button>
      </template>
    </n-modal>
  </section>
</template>

<style scoped>
.assistants {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.head__side {
  display: flex;
  gap: 8px;
  flex: 0 0 auto;
}

.page__title {
  margin: 0 0 6px;
  font-size: 16px;
  letter-spacing: 0.08em;
}

.panel {
  padding: 16px 18px;
  border: 1px solid #303a54;
  background: #0a1024;
}

.panel__title {
  margin: 0 0 12px;
  font-size: 13px;
  letter-spacing: 0.08em;
  color: #7d89b8;
}

.snippet {
  margin: 0 0 10px;
  padding: 12px 14px;
  border: 1px solid #1c2438;
  background: #04060e;
  color: #9fb4e8;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
}

.list {
  display: flex;
  flex-direction: column;
}

.row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 0;
  border-top: 1px solid #1c2438;
}

.row:first-child {
  border-top: 0;
}

.row--muted {
  opacity: 0.6;
}

.row__main {
  min-width: 0;
}

.row__name {
  font-size: 13px;
  color: #dfe6ff;
}

.row__purpose {
  margin: 4px 0 2px;
}

.row__meta {
  margin: 0;
  font-size: 12px;
}

.row__actions {
  display: flex;
  gap: 6px;
  flex: 0 0 auto;
}

.tag {
  margin-left: 8px;
  padding: 0 6px;
  border: 1px solid #303a54;
  color: #7d89b8;
  font-size: 11px;
  line-height: 16px;
}

.ghost,
.primary,
.mini {
  padding: 5px 12px;
  border: 1px solid #303a54;
  background: transparent;
  color: #dfe6ff;
  font-size: 12px;
  letter-spacing: 0.06em;
  cursor: pointer;
}

.ghost:hover,
.mini:hover {
  border-color: #4fd8eb;
  color: #4fd8eb;
}

.primary {
  border-color: #4fd8eb;
  color: #041018;
  background: #4fd8eb;
}

.primary:hover {
  background: #7ae6f4;
}

.danger:hover {
  border-color: #f26d7d;
  color: #f26d7d;
}

.form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.form__label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: #7d89b8;
}

.form__check {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #c5d0ee;
}

.secret {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid #1c2438;
  background: #04060e;
}

.secret__value {
  flex: 1 1 auto;
  min-width: 0;
  overflow-wrap: anywhere;
  color: #4fd8eb;
  font-size: 12px;
}
</style>
