#!/usr/bin/env node
// 控制台冒烟。Node 22+，只用 fetch，无第三方依赖。
// 404 的工单详情记 SKIP：这条路由可能还没挂上。列表为空时不探测详情。

const TIMEOUT_MS = 5000;
const base = (process.env.SKEIN_BASE || "http://127.0.0.1:7800").replace(/\/+$/, "");
const token = process.env.SKEIN_TOKEN || "";
const results = [];

const major = Number(process.versions.node.split(".")[0]);
if (!Number.isFinite(major) || major < 22) {
  console.log(`FAIL  运行环境  需要 Node.js 22+，当前 ${process.version}`);
  console.log("0 passed, 1 failed, 0 skipped");
  process.exit(1);
}

function report(status, name, detail) {
  results.push(status);
  console.log(detail ? `${status}  ${name}  ${detail}` : `${status}  ${name}`);
}

function finish() {
  const passed = results.filter((status) => status === "PASS").length;
  const failed = results.filter((status) => status === "FAIL").length;
  const skipped = results.filter((status) => status === "SKIP").length;
  console.log(`${passed} passed, ${failed} failed, ${skipped} skipped`);
  process.exitCode = failed > 0 ? 1 : 0;
}

function errorCode(err) {
  if (!err || typeof err !== "object") return "";
  if (typeof err.code === "string") return err.code;
  if (err.cause) {
    const code = errorCode(err.cause);
    if (code) return code;
  }
  if (Array.isArray(err.errors)) {
    for (const item of err.errors) {
      const code = errorCode(item);
      if (code) return code;
    }
  }
  return "";
}

function netError(err) {
  const names = [err?.name, err?.cause?.name];
  if (names.includes("TimeoutError") || names.includes("AbortError")) return "超时 5s";
  const code = errorCode(err);
  const known = {
    ECONNREFUSED: "连接被拒绝",
    ENOTFOUND: "域名无法解析",
    EAI_AGAIN: "域名暂时无法解析",
    ECONNRESET: "连接被重置",
    ETIMEDOUT: "超时 5s",
    UND_ERR_CONNECT_TIMEOUT: "超时 5s",
    UND_ERR_HEADERS_TIMEOUT: "超时 5s",
    UND_ERR_BODY_TIMEOUT: "超时 5s",
    UND_ERR_SOCKET: "连接中断",
  };
  if (code && known[code]) return known[code];
  const raw = err?.cause?.message || err?.message || "网络错误";
  const line = String(raw).split("\n")[0].slice(0, 180);
  if (line === "bad port") return "目标端口不可用";
  if (line === "fetch failed") return "网络错误";
  return code ? `${code} ${line}` : line;
}

function origin() {
  let url;
  try {
    url = new URL(base);
  } catch {
    return { error: `不是合法的 http(s) 地址：${base}` };
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    return { error: `不是合法的 http(s) 地址：${base}` };
  }
  if (url.pathname !== "/" && url.pathname !== "") {
    return { error: "应是源站地址，不带路径" };
  }
  return { href: url.origin };
}

async function get(path, { withToken = false } = {}) {
  const root = origin();
  if (root.error) return { error: root.error };
  const headers = {};
  if (withToken) headers["X-Skein-Token"] = token;
  try {
    const res = await fetch(new URL(path, `${root.href}/`), {
      method: "GET",
      headers,
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    const text = await res.text();
    return { status: res.status, contentType: res.headers.get("content-type") || "", text };
  } catch (err) {
    return { error: netError(err) };
  }
}

function readJson(text) {
  try {
    return { value: JSON.parse(text) };
  } catch {
    return { error: "响应不是 JSON" };
  }
}

function gaps(item, fields) {
  if (!item || typeof item !== "object" || Array.isArray(item)) return fields;
  return fields.filter((field) => item[field] === undefined || item[field] === null);
}

function visibleHtml(html) {
  return html.replace(/<!--[\s\S]*?-->/g, "");
}

async function checkHealth() {
  const name = "健康检查";
  const res = await get("/health/live");
  if (res.error) return report("FAIL", name, res.error);
  if (res.status !== 200) return report("FAIL", name, `HTTP ${res.status}`);
  const json = readJson(res.text);
  if (json.error) return report("FAIL", name, json.error);
  if (json.value?.status !== "ok") {
    return report("FAIL", name, `status=${JSON.stringify(json.value?.status ?? null)}`);
  }
  return report("PASS", name);
}

async function checkHome() {
  const name = "控制台首页";
  const res = await get("/console/");
  if (res.error) {
    report("FAIL", name, res.error);
    return "";
  }
  const html = visibleHtml(res.text);
  const problems = [];
  if (res.status !== 200) problems.push(`HTTP ${res.status}`);
  if (!res.contentType.toLowerCase().includes("text/html")) problems.push("content-type 不含 text/html");
  if (!html.includes('id="app"')) problems.push('HTML 缺少 id="app"');
  if (!html.includes("/console/assets/")) problems.push("HTML 缺少 /console/assets/");
  if (problems.length) report("FAIL", name, problems.join("；"));
  else report("PASS", name);
  return html;
}

async function checkAssets(html) {
  if (!html) return;
  const seen = new Set();
  const paths = [];
  const re = /\/console\/assets\/[^"'\\\s<>]+?\.(?:js|css)(?:\?[^"'\\\s<>]*)?(?=["'\s<>]|$)/gi;
  for (const match of html.matchAll(re)) {
    if (seen.has(match[0])) continue;
    seen.add(match[0]);
    paths.push(match[0]);
  }
  if (paths.length === 0) {
    if (html.includes("/console/assets/")) {
      report("FAIL", "静态资源", "未从 HTML 解析到 /console/assets/*.js 或 *.css");
    }
    return;
  }
  for (const path of paths) {
    const name = `静态资源 ${path}`;
    const res = await get(path);
    if (res.error) report("FAIL", name, res.error);
    else if (res.status !== 200) report("FAIL", name, `HTTP ${res.status}`);
    else report("PASS", name);
  }
}

async function checkTicketsAnonymous() {
  const name = "工单列表（无令牌）";
  const res = await get("/internal/tickets");
  if (res.error) report("FAIL", name, res.error);
  else if (res.status !== 401) report("FAIL", name, `HTTP ${res.status}`);
  else report("PASS", name);
}

async function checkCollection(name, path, key, fields) {
  if (!token) {
    report("FAIL", name, "未设置 SKEIN_TOKEN");
    return null;
  }
  const res = await get(path, { withToken: true });
  if (res.error) {
    report("FAIL", name, res.error);
    return null;
  }
  if (res.status !== 200) {
    report("FAIL", name, `HTTP ${res.status}`);
    return null;
  }
  const json = readJson(res.text);
  if (json.error) {
    report("FAIL", name, json.error);
    return null;
  }
  const list = json.value?.[key];
  if (!Array.isArray(list)) {
    report("FAIL", name, `缺少 ${key} 数组`);
    return null;
  }
  for (let index = 0; index < list.length; index += 1) {
    const missing = gaps(list[index], fields);
    if (missing.length) {
      report("FAIL", name, `第 ${index + 1} 条缺少 ${missing.join("/")}`);
      return null;
    }
  }
  report("PASS", name);
  return list;
}

async function checkDetail(id) {
  const res = await get(`/internal/tickets/${encodeURIComponent(String(id))}`, { withToken: true });
  if (res.error) return report("FAIL", "工单详情", res.error);
  if (res.status === 404) return report("SKIP", "工单详情（接口未上线）");
  if (res.status !== 200) return report("FAIL", "工单详情", `HTTP ${res.status}`);
  return report("PASS", "工单详情");
}

async function checkStream() {
  const name = "实时推送";
  const root = origin();
  if (root.error) return report("FAIL", name, root.error);
  if (!token) return report("FAIL", name, "未设置 SKEIN_TOKEN");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(new URL("/internal/stream", `${root.href}/`), {
      headers: { "X-Skein-Token": token },
      signal: controller.signal,
    });
    if (res.status !== 200) return report("FAIL", name, `HTTP ${res.status}`);
    if (!(res.headers.get("content-type") || "").includes("text/event-stream")) {
      return report("FAIL", name, "content-type 不是 text/event-stream");
    }
    const reader = res.body?.getReader();
    if (!reader) return report("FAIL", name, "响应没有可读流");
    const decoder = new TextDecoder();
    let buffer = "";
    while (!buffer.includes("event: snapshot") && buffer.length < 65536) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
    }
    await reader.cancel();
    if (!buffer.includes("event: snapshot")) return report("FAIL", name, "未收到首帧 snapshot");
    return report("PASS", name);
  } catch (err) {
    return report("FAIL", name, netError(err));
  } finally {
    clearTimeout(timer);
    controller.abort();
  }
}

try {
  if (origin().error) {
    report("FAIL", "SKEIN_BASE", origin().error);
  } else {
    await checkHealth();
    const html = await checkHome();
    await checkAssets(html);
    await checkTicketsAnonymous();
    const tickets = await checkCollection(
      "工单列表",
      "/internal/tickets?limit=5",
      "tickets",
      ["id", "code", "status"],
    );
    await checkCollection("信号列表", "/internal/signals", "signals", ["kind", "id"]);
    await checkStream();
    if (tickets?.length) await checkDetail(tickets[0].id);
  }
  finish();
} catch (err) {
  report("FAIL", "脚本", netError(err));
  finish();
}
