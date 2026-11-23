import { defineStore } from "pinia";
import {
  ApiError,
  fetchSummary,
  loginRequest,
  logoutRequest,
  probeSession,
  type LoginPayload,
  type SessionUser,
} from "./api";

let rerun = false;

type AuthMode = "" | "sso" | "local" | "token";

export const useConsoleStore = defineStore("console", {
  state: () => ({
    connected: false,
    unauthorized: true,
    booted: false,
    lastRefreshAt: null as number | null,
    error: null as string | null,
    refreshing: false,
    authMode: "" as AuthMode,
    user: null as SessionUser | null,
    permissions: [] as string[],
    ssoDown: false,
  }),
  getters: {
    /** 当前账号能不能做某件事（服务端仍会再判一次；这里只决定控件显隐） */
    can(): (permission: string) => boolean {
      return (permission: string) => this.permissions.includes(permission);
    },
    roleLabel(): string {
      switch (this.user?.role) {
        case "owner":
          return "负责人";
        case "admin":
          return "管理员";
        case "member":
          return "成员";
        default:
          return "";
      }
    },
  },
  actions: {
    clearConnection() {
      this.connected = false;
      this.unauthorized = true;
      this.user = null;
      this.permissions = [];
      this.error = null;
    },
    async refresh() {
      if (this.refreshing) {
        rerun = true;
        return;
      }
      this.refreshing = true;
      try {
        do {
          rerun = false;
          await this.probe();
        } while (rerun);
      } finally {
        this.refreshing = false;
        this.booted = true;
      }
    },
    apply(session: { authMode: AuthMode; user?: SessionUser; permissions?: string[] }) {
      this.authMode = session.authMode;
      this.user = session.user ?? null;
      this.permissions = session.permissions ?? [];
      this.connected = this.user != null;
      this.unauthorized = this.user == null;
      this.ssoDown = false;
      this.lastRefreshAt = Date.now();
    },
    async probe() {
      this.error = null;
      try {
        const session = await probeSession();
        this.authMode = session.mode;
        if (session.user) {
          this.apply({ authMode: session.mode, user: session.user, permissions: session.permissions });
          return;
        }
        // 令牌模式下还没登录：落到登录页；其他模式下没会话也是 401，走下面
        this.user = null;
        this.permissions = [];
        this.connected = false;
        this.unauthorized = true;
      } catch (err) {
        if (err instanceof ApiError && err.status === 503) {
          // 登录上游不可达：保留本地身份，不当登出处理
          this.ssoDown = true;
          this.error = null;
          if (this.user == null) {
            this.connected = false;
            this.unauthorized = true;
          }
          return;
        }
        this.connected = false;
        if (err instanceof ApiError && err.status === 401) {
          this.unauthorized = true;
          this.user = null;
          this.permissions = [];
          this.error = null;
          return;
        }
        this.error = err instanceof Error && err.message ? err.message : "网络错误";
      }
    },
    async login(payload: LoginPayload) {
      const session = await loginRequest(payload);
      this.apply({ authMode: session.mode, user: session.user, permissions: session.permissions });
    },
    async logout() {
      try {
        await logoutRequest();
      } catch {
        // 登录上游不可达也要把本地状态清掉
      }
      this.clearConnection();
    },
    async ensureFresh() {
      // 某些页面打开前顺手验一下会话，避免展示一个已经过期的界面
      if (!this.connected) return;
      try {
        await fetchSummary();
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) this.clearConnection();
      }
    },
  },
});
