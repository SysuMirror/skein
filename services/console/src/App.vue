<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  darkTheme,
  dateZhCN,
  zhCN,
  NButton,
  NConfigProvider,
  NDialogProvider,
  NInput,
  NMessageProvider,
  type GlobalThemeOverrides,
} from "naive-ui";
import ErrorBar from "./components/ErrorBar.vue";
import SkyCanvas from "./components/SkyCanvas.vue";
import { ApiError } from "./api";
import { useLiveStore } from "./live";
import { useConsoleStore } from "./store";

const store = useConsoleStore();
const live = useLiveStore();
const route = useRoute();
const router = useRouter();
const tokenDraft = ref("");
const submitted = ref(false);
const emailDraft = ref("");
const passwordDraft = ref("");
const loginBusy = ref(false);
const loginError = ref("");

// 这个部署用哪种登录方式由控制面说了算（探测接口里的 mode）
const tokenMode = computed(() => store.authMode === "token");
const passwordMode = computed(() => store.authMode === "local" || store.authMode === "sso");

const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: "#4fd8eb",
    primaryColorHover: "#7ae6f4",
    primaryColorPressed: "#2fb8cc",
    primaryColorSuppl: "#4fd8eb",
    infoColor: "#6ea8fe",
    infoColorHover: "#8ebbff",
    infoColorPressed: "#4d8eeb",
    infoColorSuppl: "#6ea8fe",
    successColor: "#57d9a3",
    successColorHover: "#7ee4b8",
    successColorPressed: "#36c48a",
    successColorSuppl: "#57d9a3",
    warningColor: "#f5b942",
    warningColorHover: "#ffcb6b",
    warningColorPressed: "#e0a428",
    warningColorSuppl: "#f5b942",
    errorColor: "#f26d7d",
    errorColorHover: "#ff8b98",
    errorColorPressed: "#e05264",
    errorColorSuppl: "#f26d7d",
    bodyColor: "#04060e",
    cardColor: "#0a1024",
    modalColor: "#0a1024",
    popoverColor: "#10182e",
    tableColor: "#0a1024",
    tableHeaderColor: "#10182e",
    tableColorHover: "rgba(79, 216, 235, 0.06)",
    tableColorStriped: "#0c1428",
    inputColor: "transparent",
    hoverColor: "rgba(79, 216, 235, 0.08)",
    pressedColor: "rgba(79, 216, 235, 0.14)",
    borderColor: "#303a54",
    dividerColor: "#303a54",
    borderRadius: "0",
    borderRadiusSmall: "0",
    textColorBase: "#dfe6ff",
    textColor1: "#dfe6ff",
    textColor2: "#c5d0ee",
    textColor3: "#7d89b8",
    placeholderColor: "#7d89b8",
    iconColor: "#7d89b8",
    iconColorHover: "#4fd8eb",
    scrollbarColor: "rgba(79, 216, 235, 0.35)",
    scrollbarColorHover: "rgba(79, 216, 235, 0.55)",
    fontFamily: '"Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
    fontFamilyMono: '"Cascadia Mono", "Sarasa Mono SC", ui-monospace, monospace',
    fontSize: "13px",
    fontSizeSmall: "12px",
  },
  Menu: {
    color: "transparent",
    itemColorHover: "transparent",
    itemColorActive: "transparent",
    itemColorActiveHover: "transparent",
    itemColorActiveCollapsed: "transparent",
    itemTextColor: "#7d89b8",
    itemTextColorHover: "#4fd8eb",
    itemTextColorActive: "#4fd8eb",
    itemTextColorActiveHover: "#4fd8eb",
    itemTextColorHorizontal: "#7d89b8",
    itemTextColorHoverHorizontal: "#4fd8eb",
    itemTextColorActiveHorizontal: "#4fd8eb",
    itemTextColorActiveHoverHorizontal: "#4fd8eb",
    itemIconColor: "#7d89b8",
    itemIconColorHover: "#4fd8eb",
    itemIconColorActive: "#4fd8eb",
    itemIconColorActiveHover: "#4fd8eb",
    arrowColor: "#7d89b8",
    arrowColorHover: "#4fd8eb",
    arrowColorActive: "#4fd8eb",
    arrowColorActiveHover: "#4fd8eb",
    borderRadius: "0px",
  },
  Input: {
    color: "transparent",
    colorHover: "transparent",
    colorFocus: "transparent",
    textColor: "#dfe6ff",
    placeholderColor: "#7d89b8",
    caretColor: "#4fd8eb",
    iconColor: "#7d89b8",
    iconColorHover: "#4fd8eb",
    border: "1px solid transparent",
    borderHover: "1px solid transparent",
    borderFocus: "1px solid transparent",
    boxShadowFocus: "none",
    borderRadius: "0",
  },
  Button: {
    color: "transparent",
    colorHover: "rgba(79, 216, 235, 0.08)",
    colorPressed: "rgba(79, 216, 235, 0.14)",
    colorFocus: "transparent",
    colorQuaternary: "transparent",
    colorQuaternaryHover: "transparent",
    colorQuaternaryPressed: "transparent",
    textColor: "#dfe6ff",
    textColorHover: "#4fd8eb",
    textColorPressed: "#2fb8cc",
    textColorFocus: "#dfe6ff",
    border: "1px solid #303a54",
    borderHover: "1px solid #4fd8eb",
    borderPressed: "1px solid #2fb8cc",
    borderFocus: "1px solid #4fd8eb",
    rippleColor: "transparent",
    colorPrimary: "#4fd8eb",
    colorHoverPrimary: "#7ae6f4",
    colorPressedPrimary: "#2fb8cc",
    colorFocusPrimary: "#4fd8eb",
    textColorPrimary: "#041018",
    textColorHoverPrimary: "#041018",
    textColorPressedPrimary: "#041018",
    textColorFocusPrimary: "#041018",
    borderPrimary: "1px solid #4fd8eb",
    borderHoverPrimary: "1px solid #7ae6f4",
    borderPressedPrimary: "1px solid #2fb8cc",
    borderFocusPrimary: "1px solid #4fd8eb",
    rippleColorPrimary: "transparent",
  },
};

// 导航只显示当前账号用得上的入口；服务端仍会再判一次权限
const navItems = computed(() =>
  [
    { label: "工作台", key: "/today", show: true },
    { label: "Agent", key: "/agent", show: store.can("agent.chat") },
    { label: "需求", key: "/tickets", show: true },
    { label: "运行", key: "/fleet", show: true },
  ].filter((item) => item.show),
);

const RUNS_NAV = computed(() =>
  [
    { label: "总览", key: "/fleet", show: true },
    { label: "节点", key: "/nodes", show: true },
    { label: "Worker", key: "/workers", show: true },
    { label: "事件", key: "/events", show: true },
    { label: "会话", key: "/calls", show: true },
    { label: "信号", key: "/signals", show: true },
    { label: "助手", key: "/assistants", show: store.can("assistant.manage") },
  ].filter((item) => item.show),
);

const booting = computed(() => !store.booted);

const landing = computed(() => store.booted && !store.connected);

// 503 降级：SSO 上游挂了但本地还有身份，继续用，顶部挂提示。
const degraded = computed(() => store.booted && store.ssoDown && store.user != null);

const LOGIN_REJECTIONS: Record<string, string> = {
  not_internal: "该账号不是内部成员",
  not_admin: "该账号没有访问控制台的权限",
  account_disabled: "该账号已停用",
  password_verify_forbidden: "应用未开通密码校验，请联系管理员",
};

function loginErrorText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 503) return "登录服务暂时不可用，稍后再试";
    if (err.status === 429) return "尝试次数过多，请稍后再试";
    if (err.status === 403) return LOGIN_REJECTIONS[err.message] ?? "没有访问权限";
    if (err.status === 401) return "邮箱或密码不正确";
    if (err.status === 400) return "请输入登录信息";
    return err.message || "登录失败";
  }
  return err instanceof Error && err.message ? err.message : "登录失败";
}

function tokenErrorText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "令牌无效";
    if (err.status === 429) return "尝试次数过多，请稍后再试";
    if (err.status === 400) return "请填写控制台令牌";
    if (err.status === 503) return "控制面暂时不可用，稍后再试";
    return err.message || "登录失败";
  }
  return err instanceof Error && err.message ? err.message : "登录失败";
}

async function applyLogin() {
  if (loginBusy.value) return;
  loginError.value = "";
  const email = emailDraft.value.trim();
  if (!email || !passwordDraft.value) {
    loginError.value = "请输入邮箱和密码";
    return;
  }
  loginBusy.value = true;
  try {
    await store.login({ email, password: passwordDraft.value });
    passwordDraft.value = "";
  } catch (err) {
    loginError.value = loginErrorText(err);
  } finally {
    loginBusy.value = false;
  }
}

async function applyLogout() {
  await store.logout();
}

const feedLabel = computed(() => {
  if (!store.connected) return "未连接";
  if (live.state === "open") return "实时";
  if (live.state === "connecting") return "连接中";
  return "轮询";
});

watch(
  () => store.connected,
  (connected) => {
    if (connected) live.start();
    else live.stop();
  },
  { immediate: true },
);

// 401 会把 store.error 清掉，拒绝原因要在落地上单独说清楚。
const gateMessage = computed(() => {
  if (store.connected) return "";
  if (store.error) return store.error;
  if (store.refreshing) return "";
  return "";
});

const refreshLabel = computed(() =>
  store.lastRefreshAt == null ? "尚未刷新" : formatStamp(store.lastRefreshAt),
);

const refreshIso = computed(() =>
  store.lastRefreshAt == null ? undefined : new Date(store.lastRefreshAt).toISOString(),
);

function formatStamp(ts: number): string {
  const date = new Date(ts);
  const pad = (value: number) => String(value).padStart(2, "0");
  const month = pad(date.getMonth() + 1);
  const day = pad(date.getDate());
  const hours = pad(date.getHours());
  const minutes = pad(date.getMinutes());
  const seconds = pad(date.getSeconds());
  return `${date.getFullYear()}-${month}-${day} ${hours}:${minutes}:${seconds}`;
}

async function applyToken() {
  if (loginBusy.value) return;
  submitted.value = true;
  loginError.value = "";
  const value = tokenDraft.value.trim();
  if (!value) {
    loginError.value = "请填写控制台令牌";
    return;
  }
  loginBusy.value = true;
  try {
    await store.login({ token: value });
    tokenDraft.value = "";
  } catch (err) {
    loginError.value = tokenErrorText(err);
  } finally {
    loginBusy.value = false;
  }
}

const section = computed<"runs" | null>(() => {
  if (
    ["/fleet", "/nodes", "/workers", "/events", "/calls", "/signals", "/assistants"].includes(
      route.path,
    )
  ) {
    return "runs";
  }
  return null;
});

const subItems = computed(() => (section.value === "runs" ? RUNS_NAV.value : []));

function navActive(key: string) {
  if (key === "/fleet") return section.value === "runs";
  return route.path === key || route.path.startsWith(`${key}/`);
}

function openNav(key: string) {
  void router.push(key);
}

onMounted(() => {
  void store.refresh();
});
</script>

<template>
  <n-config-provider
    class="app-root"
    :theme="darkTheme"
    :theme-overrides="themeOverrides"
    :locale="zhCN"
    :date-locale="dateZhCN"
  >
    <n-message-provider>
      <n-dialog-provider>
        <div class="hud-corner hud-tl" aria-hidden="true"></div>
        <div class="hud-corner hud-tr" aria-hidden="true"></div>
        <div class="hud-corner hud-bl" aria-hidden="true"></div>
        <div class="hud-corner hud-br" aria-hidden="true"></div>

        <div v-if="booting" class="boot" role="status" aria-live="polite">
          <span class="boot__dot" aria-hidden="true"></span>
          <span>正在连接控制面…</span>
        </div>

        <div v-else-if="landing" class="landing">
          <sky-canvas :landing="true" />
          <div class="landing__content">
            <h1>
              <span>雁</span><span>阵</span>
            </h1>
            <p class="landing__pitch">把真实用户反馈，变成可验收的修复</p>
            <p class="landing__sub">需求即命令 · 会话即 Worker · 账本即阵型</p>
            <form v-if="passwordMode" class="landing__form" @submit.prevent="applyLogin">
              <n-input
                v-model:value="emailDraft"
                class="token-field landing__token"
                placeholder="邮箱"
                autocomplete="username"
                :input-props="{ autocomplete: 'username', spellcheck: false, 'aria-label': '邮箱' }"
                @keyup.enter="applyLogin"
              />
              <n-input
                v-model:value="passwordDraft"
                class="token-field landing__token landing__stack"
                type="password"
                placeholder="密码"
                show-password-on="click"
                autocomplete="current-password"
                :input-props="{ autocomplete: 'current-password', spellcheck: false, 'aria-label': '密码' }"
                @keyup.enter="applyLogin"
              />
              <button class="landing__enter" type="submit" :aria-busy="loginBusy">
                登 录 →
              </button>
              <p v-if="loginError" class="landing__error" role="alert">{{ loginError }}</p>
              <p v-else-if="store.ssoDown" class="landing__error" role="status">
                登录服务暂时不可用，稍后再试
              </p>
            </form>
            <form v-else-if="tokenMode" class="landing__form" @submit.prevent="applyToken">
              <n-input
                v-model:value="tokenDraft"
                class="token-field landing__token"
                type="password"
                placeholder="控制面令牌"
                autocomplete="off"
                :input-props="{ autocomplete: 'off', spellcheck: false, 'aria-label': '控制面令牌' }"
                @keyup.enter="applyToken"
              />
              <button class="landing__enter" type="submit" :aria-busy="loginBusy">
                接 入 调 度 台 →
              </button>
              <p v-if="loginError || gateMessage" class="landing__error" role="alert">
                {{ loginError || gateMessage }}
              </p>
            </form>
            <p v-else-if="store.error" class="landing__error" role="alert">{{ store.error }}</p>
            <router-link to="/about" class="landing__about">这是什么？</router-link>
          </div>
        </div>

        <div v-else class="shell">
          <header class="topbar">
            <div class="brand">
              <h1>雁阵</h1>
              <span class="brand__sub">SKEIN // AGENT GRID</span>
            </div>
            <nav class="nav" aria-label="主导航">
              <button
                v-for="item in navItems"
                :key="item.key"
                type="button"
                class="nav__item"
                :class="{ active: navActive(item.key) }"
                :aria-current="navActive(item.key) ? 'page' : undefined"
                @click="openNav(item.key)"
              >
                {{ item.label }}
              </button>
            </nav>
            <div class="topbar__meta">
              <div class="conn" :class="{ live: store.connected, push: live.state === 'open' }">
                <span class="conn__dot" aria-hidden="true"></span>
                <span>{{ feedLabel }}</span>
              </div>
              <div class="topbar__clock">
                <span>最近刷新</span>
                <time :key="store.lastRefreshAt ?? 0" class="mono" :datetime="refreshIso">{{ refreshLabel }}</time>
              </div>
              <n-button
                class="topbar__refresh"
                quaternary
                size="small"
                :loading="store.refreshing"
                @click="store.refresh()"
              >
                刷新
              </n-button>
              <div v-if="store.user" class="whoami">
                <span class="whoami__name">{{ store.user.name || store.user.email }}</span>
                <span v-if="store.roleLabel" class="whoami__role">{{ store.roleLabel }}</span>
                <button type="button" class="whoami__out" @click="applyLogout">退出</button>
              </div>
            </div>
          </header>
          <nav v-if="subItems.length" class="subnav" aria-label="二级导航">
            <button
              v-for="item in subItems"
              :key="item.key"
              type="button"
              class="subnav__item"
              :class="{ active: route.path === item.key }"
              :aria-current="route.path === item.key ? 'page' : undefined"
              @click="openNav(item.key)"
            >
              {{ item.label }}
            </button>
          </nav>
          <main class="main">
            <error-bar
              v-if="degraded"
              message="登录服务暂时不可用，已保留本地登录状态"
              @retry="store.refresh()"
            />
            <error-bar v-if="store.error" :message="store.error" @retry="store.refresh()" />
            <router-view />
          </main>
        </div>
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>
