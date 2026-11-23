// 本地预览代理：把控制台 dist 和本机控制面拼到同一个源上（零依赖）。
// 用法：node deploy/dev/preview-proxy.mjs <dist目录> [控制面=http://127.0.0.1:7800] [端口=5173]
import { createServer, request as httpRequest } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { join, extname, resolve } from "node:path";

const [distArg, upstreamArg, portArg] = process.argv.slice(2);
if (!distArg) {
  console.error("usage: node preview-proxy.mjs <dist-dir> [upstream] [port]");
  process.exit(1);
}
const DIST = resolve(distArg);
const UPSTREAM = new URL(upstreamArg || "http://127.0.0.1:7800");
const PORT = Number(portArg || 5173);

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
};

const PROXY_PREFIXES = ["/internal", "/api", "/ops", "/health"];

function proxy(req, res) {
  const upstream = httpRequest(
    {
      host: UPSTREAM.hostname,
      port: UPSTREAM.port || 80,
      path: req.url,
      method: req.method,
      headers: { ...req.headers, host: UPSTREAM.host },
    },
    (up) => {
      res.writeHead(up.statusCode ?? 502, up.headers);
      up.pipe(res);
      up.on("error", () => res.destroy());
    },
  );
  upstream.on("error", (err) => {
    if (res.headersSent) {
      res.destroy();
      return;
    }
    res.writeHead(502, { "content-type": "application/json" });
    res.end(JSON.stringify({ detail: `代理失败：${err.message}` }));
  });
  res.on("close", () => upstream.destroy());
  req.pipe(upstream);
}

async function serveFile(res, file) {
  try {
    const info = await stat(file);
    if (info.isDirectory()) return serveFile(res, join(file, "index.html"));
    const body = await readFile(file);
    res.writeHead(200, { "content-type": TYPES[extname(file)] ?? "application/octet-stream" });
    res.end(body);
  } catch {
    res.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
    res.end("not found");
  }
}

createServer(async (req, res) => {
  const url = (req.url ?? "/").split("?")[0];
  if (PROXY_PREFIXES.some((prefix) => url.startsWith(prefix))) {
    proxy(req, res);
    return;
  }
  if (url === "/" || url === "") {
    res.writeHead(302, { location: "/console/" });
    res.end();
    return;
  }
  const clean = decodeURIComponent(url).replace(/^\/+/, "").replace(/^console\//, "").replace(/\.\./g, "");
  await serveFile(res, join(DIST, clean));
}).listen(PORT, "127.0.0.1", () => {
  console.log(`preview on http://127.0.0.1:${PORT}/console/ → ${UPSTREAM.origin}`);
});
