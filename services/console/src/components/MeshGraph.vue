<script setup lang="ts">
// 总览力导网：节点 / 工单 / 会话。斥力、弹簧、锚点把布局拉开，冷却后停算。
// 视觉签名不变则不替换场景，节点 DOM 保持可点。
import { computed, onBeforeUnmount, ref, shallowRef, watch } from "vue";
import { useRouter } from "vue-router";
import type {
  Dispatch,
  NodeRecord,
  SessionRecord,
  Ticket,
} from "../api";
import EmptyState from "./EmptyState.vue";

const TICKET_CAP = 20;
const MESH_H = 460;
const DISPATCH_REST = 140;
const SESSION_REST = 40;
const MESH_CUTOFF = 260;
const MESH_REPULSE = 1500;
const SPRING_K = 0.012;
const SLOT_K = 0.1;
const ANCHOR_X = 0.004;
const ANCHOR_Y = 0.002;
const DAMP = 0.7;
const SESSION_DAMP = 0.55;
const MAX_SPEED = 12;
const QUIET = 0.5;
const QUIET_FRAMES = 12;
const FRAME_CAP = 480;

type Kind = "node" | "ticket" | "session";

interface Source {
  nodes: NodeRecord[];
  tickets: Ticket[];
  sessions: SessionRecord[];
  dispatches: Array<{ nodeId: string; ticketId: string }>;
  hiddenDispatches: number;
  ticketTotal: number;
  empty: boolean;
}

interface Body {
  key: string;
  kind: Kind;
  id: string;
  label: string;
  sub: string;
  title: string;
  color: string;
  off: boolean;
  ax: number;
  ay: number;
  anchorKey: string | null;
}

interface Link {
  key: string;
  kind: "dispatch" | "attach";
  a: string;
  b: string;
}

interface Pinned {
  key: string;
  id: string;
  x: number;
  y: number;
  title: string;
  color: string;
}

interface Built {
  bodies: Body[];
  pinned: Pinned[];
  links: Link[];
  hiddenOrphans: number;
}

interface Glyph {
  key: string;
  kind: Kind;
  id: string;
  x: number;
  y: number;
  label: string;
  sub: string;
  title: string;
  color: string;
  off: boolean;
}

interface Edge {
  key: string;
  kind: "dispatch";
  d: string;
}

interface Scene {
  w: number;
  h: number;
  glyphs: Glyph[];
  edges: Edge[];
  hiddenOrphans: number;
}

interface Point {
  x: number;
  y: number;
  vx: number;
  vy: number;
}

interface Pad {
  x: number;
  top: number;
  limitY: number;
}

const props = defineProps<{
  nodes: NodeRecord[];
  tickets: Ticket[];
  dispatches: Dispatch[];
  sessions: SessionRecord[];
}>();

const router = useRouter();
const mesh = ref<HTMLElement | null>(null);
const selectedKey = ref<string | null>(null);
const scene = shallowRef<Scene>(blankScene());

const pos = new Map<string, Point>();
let meshEl: HTMLElement | null = null;
let observer: ResizeObserver | null = null;
let raf = 0;
let running = false;
let cool = 0;
let frames = 0;
let lastW = 0;
let lastStructure = "";
let lastVisual = "";
let stableFrames = 0;

const source = computed(() =>
  buildSource(
    props.nodes,
    props.tickets,
    props.dispatches,
    props.sessions,
  ),
);
const empty = computed(() => source.value.empty);
const structureKey = computed(() => signatureOf(source.value));
const displayKey = computed(() => `${structureKey.value}||${labelsOf(source.value)}`);
const info = computed(() => describeSelection(selectedKey.value, props.nodes, props.tickets, props.sessions));
const footnote = computed(() => {
  const data = source.value;
  const parts: string[] = [];
  if (data.ticketTotal > TICKET_CAP) parts.push(`图中工单为最近 ${TICKET_CAP} 张`);
  if (data.hiddenDispatches > 0) parts.push(`另有 ${data.hiddenDispatches} 条派单未画出`);
  if (scene.value.hiddenOrphans > 0) {
    parts.push(`另有 ${scene.value.hiddenOrphans} 个未关联会话未画出`);
  }
  return parts.join("；");
});

watch(displayKey, () => {
  syncSelection();
  if (source.value.empty) {
    stop();
    lastStructure = structureKey.value;
    lastVisual = "";
    scene.value = blankScene();
    return;
  }
  if (structureKey.value !== lastStructure) {
    lastStructure = structureKey.value;
    wake();
    return;
  }
  if (!running) publishNow();
});

watch(
  mesh,
  (el) => {
    observer?.disconnect();
    observer = null;
    meshEl = el;
    if (!el || source.value.empty) return;
    lastW = Math.floor(el.clientWidth);
    observer = new ResizeObserver(() => {
      if (!meshEl || source.value.empty) return;
      const width = Math.floor(meshEl.clientWidth);
      if (Math.abs(width - lastW) < 2) return;
      lastW = width;
      wake();
    });
    observer.observe(el);
    wake();
  },
  { flush: "post" },
);

onBeforeUnmount(() => {
  stop();
  observer?.disconnect();
  observer = null;
});

function pick(glyph: Glyph): void {
  if (glyph.kind === "ticket") {
    void router.push("/tickets");
    return;
  }
  selectedKey.value = selectedKey.value === glyph.key ? null : glyph.key;
}

function clearSelection(): void {
  selectedKey.value = null;
}

function wake(): void {
  if (source.value.empty) {
    stop();
    return;
  }
  lastStructure = structureKey.value;
  frames = 0;
  cool = 0;
  stableFrames = 0;
  if (running) return;
  running = true;
  tick();
}

function stop(): void {
  running = false;
  if (raf !== 0) cancelAnimationFrame(raf);
  raf = 0;
}

function tick(): void {
  if (!running) return;
  const width = Math.floor(meshEl?.clientWidth ?? 0);
  if (width < 40) {
    raf = requestAnimationFrame(tick);
    return;
  }
  const cooled = step(width, MESH_H);
  if (!running || cooled) {
    running = false;
    raf = 0;
    return;
  }
  raf = requestAnimationFrame(tick);
}

function publishNow(): void {
  const width = Math.floor(meshEl?.clientWidth ?? 0);
  if (width < 40 || source.value.empty) return;
  const built = layout(width, MESH_H);
  const pad = pads(width, MESH_H, built.pinned.length > 0);
  ensurePositions(built.bodies, width, pad);
  publish(toScene(built, width, MESH_H));
}

function step(width: number, height: number): boolean {
  const built = layout(width, height);
  const pad = pads(width, height, built.pinned.length > 0);
  ensurePositions(built.bodies, width, pad);
  const energy = applyForces(built, width, pad);
  const unchanged = publish(toScene(built, width, height));
  frames += 1;
  stableFrames = unchanged ? stableFrames + 1 : 0;
  const population = Math.max(1, built.bodies.length);
  cool = energy / population < QUIET ? cool + 1 : 0;
  // 画面已经不动，或能量降下来，就停掉 rAF，避免收敛后还在漂。
  if (cool > QUIET_FRAMES || stableFrames > QUIET_FRAMES || frames > FRAME_CAP) {
    for (const point of pos.values()) {
      point.vx = 0;
      point.vy = 0;
    }
    return true;
  }
  return false;
}

function syncSelection(): void {
  const key = selectedKey.value;
  if (!key) return;
  const split = key.indexOf(":");
  const kind = key.slice(0, split);
  const id = key.slice(split + 1);
  const data = source.value;
  const alive =
    kind === "node"
      ? data.nodes.some((node) => node.id === id)
      : kind === "session"
        ? data.sessions.some((session) => session.id === id)
        : false;
  if (!alive) selectedKey.value = null;
}

function blankScene(): Scene {
  return { w: 0, h: MESH_H, glyphs: [], edges: [], hiddenOrphans: 0 };
}

function pads(width: number, height: number, hasOrphans: boolean): Pad {
  const x = Math.min(64, Math.max(18, width * 0.06));
  const top = 40;
  const bottom = hasOrphans ? 86 : 58;
  return { x, top, limitY: Math.max(top + 8, height - bottom) };
}

function layout(width: number, height: number): Built {
  const data = source.value;
  const nodeIds = new Set(data.nodes.map((node) => node.id));
  const bodies: Body[] = [];
  for (const node of data.nodes) {
    const tone = nodeTone(node.status);
    bodies.push({
      key: `node:${node.id}`,
      kind: "node",
      id: node.id,
      label: clip(node.name.trim() || node.id, 12),
      sub: tone.label,
      title: nodeTitle(node),
      color: tone.color,
      off: tone.off,
      ax: 0.32,
      ay: 0.46,
      anchorKey: null,
    });
  }
  for (const ticket of data.tickets) {
    const tone = ticketTone(ticket.status);
    const code = ticket.code.trim() || ticket.id;
    bodies.push({
      key: `ticket:${ticket.id}`,
      kind: "ticket",
      id: ticket.id,
      label: clip(code, 12),
      sub: tone.label,
      title: ticketTitle(ticket),
      color: tone.color,
      off: false,
      ax: 0.7,
      ay: 0.44,
      anchorKey: null,
    });
  }

  const attached: SessionRecord[] = [];
  const orphans: SessionRecord[] = [];
  for (const session of data.sessions) {
    if (session.node_id && nodeIds.has(session.node_id)) attached.push(session);
    else orphans.push(session);
  }
  for (const session of attached) {
    bodies.push({
      key: `session:${session.id}`,
      kind: "session",
      id: session.id,
      label: "",
      sub: "",
      title: sessionTitle(session, props.nodes, props.tickets),
      color: "var(--cyan)",
      off: false,
      ax: 0.32,
      ay: 0.46,
      anchorKey: `node:${session.node_id}`,
    });
  }

  const links: Link[] = [];
  for (const edge of data.dispatches) {
    links.push({
      key: `dispatch:${edge.nodeId}|${edge.ticketId}`,
      kind: "dispatch",
      a: `node:${edge.nodeId}`,
      b: `ticket:${edge.ticketId}`,
    });
  }
  for (const session of attached) {
    links.push({
      key: `attach:${session.id}`,
      kind: "attach",
      a: `node:${session.node_id}`,
      b: `session:${session.id}`,
    });
  }

  const pinned = placeOrphans(orphans, width, height);
  // 先放节点，会话才能挂到已经存在的锚点上。绘制顺序在 toScene 里另排。
  bodies.sort((a, b) => initRank(a.kind) - initRank(b.kind) || a.key.localeCompare(b.key));
  return {
    bodies,
    pinned: pinned.shown,
    links,
    hiddenOrphans: pinned.hidden,
  };
}

function placeOrphans(
  sessions: SessionRecord[],
  width: number,
  height: number,
): { shown: Pinned[]; hidden: number } {
  const ordered = [...sessions].sort((a, b) => a.id.localeCompare(b.id));
  const margin = 22;
  const minGap = 16;
  const usable = Math.max(minGap, width - margin * 2);
  const maxFit = Math.max(1, Math.floor(usable / minGap));
  const shown = ordered.slice(0, maxFit);
  const count = shown.length;
  const gap = count <= 1 ? 0 : Math.min(36, usable / (count - 1));
  const span = gap * Math.max(0, count - 1);
  const start = (width - span) / 2;
  const y = height - 18;
  return {
    shown: shown.map((session, index) => ({
      key: `session:${session.id}`,
      id: session.id,
      x: round1(start + gap * index),
      y,
      title: sessionTitle(session, props.nodes, props.tickets),
      color: "var(--muted)",
    })),
    hidden: Math.max(0, ordered.length - shown.length),
  };
}

function ensurePositions(bodies: Body[], width: number, pad: Pad): void {
  const live = new Set(bodies.map((body) => body.key));
  for (const key of pos.keys()) {
    if (!live.has(key)) pos.delete(key);
  }
  for (const body of bodies) {
    if (pos.has(body.key)) continue;
    if (body.kind === "session" && body.anchorKey) {
      const host = pos.get(body.anchorKey);
      const theta = sessionAngle(body.key);
      const x = host ? host.x + Math.cos(theta) * SESSION_REST : width * body.ax;
      const y = host ? host.y + Math.sin(theta) * SESSION_REST : MESH_H * body.ay;
      pos.set(body.key, { x, y, vx: 0, vy: 0 });
      continue;
    }
    const jx = (unit(body.key) - 0.5) * Math.min(160, width * 0.2);
    const jy = (unit(`${body.key}#`) - 0.5) * Math.min(100, MESH_H * 0.18);
    pos.set(body.key, {
      x: clamp(width * body.ax + jx, pad.x, width - pad.x),
      y: clamp(MESH_H * body.ay + jy, pad.top, pad.limitY),
      vx: 0,
      vy: 0,
    });
  }
}

function applyForces(built: Built, width: number, pad: Pad): number {
  const meshKeys = built.bodies.filter((body) => body.kind !== "session").map((body) => body.key);
  repel(meshKeys, MESH_CUTOFF, MESH_REPULSE);
  const byHost = new Map<string, string[]>();
  for (const body of built.bodies) {
    if (body.kind !== "session" || !body.anchorKey) continue;
    const list = byHost.get(body.anchorKey) ?? [];
    list.push(body.key);
    byHost.set(body.anchorKey, list);
  }
  for (const list of byHost.values()) repel(list, 36, 180);

  for (const link of built.links) {
    if (link.kind === "attach") {
      pullToSlot(link);
      continue;
    }
    const a = pos.get(link.a);
    const b = pos.get(link.b);
    if (!a || !b) continue;
    spring(a, b, DISPATCH_REST, SPRING_K);
  }

  let energy = 0;
  for (const body of built.bodies) {
    const point = pos.get(body.key);
    if (!point) continue;
    if (body.kind !== "session") {
      point.vx += (width * body.ax - point.x) * ANCHOR_X;
      point.vy += (MESH_H * body.ay - point.y) * ANCHOR_Y;
    }
    const damp = body.kind === "session" ? SESSION_DAMP : DAMP;
    point.vx *= damp;
    point.vy *= damp;
    const speed = Math.hypot(point.vx, point.vy);
    if (speed > MAX_SPEED) {
      point.vx = (point.vx / speed) * MAX_SPEED;
      point.vy = (point.vy / speed) * MAX_SPEED;
    }
    const nextX = point.x + point.vx;
    const nextY = point.y + point.vy;
    if (nextX <= pad.x || nextX >= width - pad.x) point.vx = 0;
    if (nextY <= pad.top || nextY >= pad.limitY) point.vy = 0;
    point.x = clamp(nextX, pad.x, width - pad.x);
    point.y = clamp(nextY, pad.top, pad.limitY);
    energy += Math.abs(point.vx) + Math.abs(point.vy);
  }
  return energy;
}

function pullToSlot(link: Link): void {
  const host = pos.get(link.a);
  const session = pos.get(link.b);
  if (!host || !session) return;
  const theta = sessionAngle(link.b);
  const targetX = host.x + Math.cos(theta) * SESSION_REST;
  const targetY = host.y + Math.sin(theta) * SESSION_REST;
  session.vx += (targetX - session.x) * SLOT_K;
  session.vy += (targetY - session.y) * SLOT_K;
}

function repel(keys: string[], cutoff: number, gain: number): void {
  const reach = cutoff * cutoff;
  for (let i = 0; i < keys.length; i += 1) {
    const a = pos.get(keys[i] ?? "");
    if (!a) continue;
    for (let j = i + 1; j < keys.length; j += 1) {
      const b = pos.get(keys[j] ?? "");
      if (!b) continue;
      let dx = a.x - b.x;
      let dy = a.y - b.y;
      let d2 = dx * dx + dy * dy;
      if (d2 < 0.01) {
        dx = 0.1;
        dy = 0;
        d2 = 0.01;
      }
      if (d2 >= reach) continue;
      const dist = Math.sqrt(d2);
      const force = gain / d2;
      const ux = (dx / dist) * force;
      const uy = (dy / dist) * force;
      a.vx += ux;
      a.vy += uy;
      b.vx -= ux;
      b.vy -= uy;
    }
  }
}

function spring(a: Point, b: Point, rest: number, k: number): void {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const dist = Math.hypot(dx, dy) || 1;
  const force = (dist - rest) * k;
  const ux = (dx / dist) * force;
  const uy = (dy / dist) * force;
  a.vx += ux;
  a.vy += uy;
  b.vx -= ux;
  b.vy -= uy;
}

function toScene(built: Built, width: number, height: number): Scene {
  const glyphs: Glyph[] = [];
  for (const body of built.bodies) {
    const point = pos.get(body.key);
    if (!point) continue;
    glyphs.push({
      key: body.key,
      kind: body.kind,
      id: body.id,
      x: round1(point.x),
      y: round1(point.y),
      label: body.label,
      sub: body.sub,
      title: body.title,
      color: body.color,
      off: body.off,
    });
  }
  for (const item of built.pinned) {
    glyphs.push({
      key: item.key,
      kind: "session",
      id: item.id,
      x: item.x,
      y: item.y,
      label: "",
      sub: "",
      title: item.title,
      color: item.color,
      off: true,
    });
  }
  glyphs.sort((a, b) => kindRank(a.kind) - kindRank(b.kind) || a.key.localeCompare(b.key));

  const edges: Edge[] = [];
  for (const link of built.links) {
    if (link.kind === "attach") continue;
    const a = pos.get(link.a);
    const b = pos.get(link.b);
    if (!a || !b) continue;
    edges.push({
      key: link.key,
      kind: link.kind,
      d: curve(round1(a.x), round1(a.y), round1(b.x), round1(b.y), link.key),
    });
  }
  edges.sort((a, b) => a.key.localeCompare(b.key));
  return { w: width, h: height, glyphs, edges, hiddenOrphans: built.hiddenOrphans };
}

function publish(next: Scene): boolean {
  const visual = visualOf(next);
  if (visual === lastVisual) return true;
  lastVisual = visual;
  scene.value = next;
  return false;
}

function visualOf(next: Scene): string {
  const glyphs = next.glyphs.map((glyph) =>
    `${glyph.key}@${glyph.x},${glyph.y},${glyph.label},${glyph.sub},${glyph.color}`,
  );
  const edges = next.edges.map((edge) => edge.key);
  return `${next.w}x${next.h}#${next.hiddenOrphans}|${glyphs.join(";")}|${edges.join(";")}`;
}

function curve(x1: number, y1: number, x2: number, y2: number, key: string): string {
  const mx = (x1 + x2) / 2;
  const my = (y1 + y2) / 2;
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len = Math.hypot(dx, dy) || 1;
  const bend = (unit(key) - 0.5) * 28;
  const cx = mx + (-dy / len) * bend;
  const cy = my + (dx / len) * bend;
  return `M${x1.toFixed(1)} ${y1.toFixed(1)} Q${cx.toFixed(1)} ${cy.toFixed(1)} ${x2.toFixed(1)} ${y2.toFixed(1)}`;
}

function buildSource(
  nodesIn: readonly NodeRecord[] | null | undefined,
  ticketsIn: readonly Ticket[] | null | undefined,
  dispatchesIn: readonly Dispatch[] | null | undefined,
  sessionsIn: readonly SessionRecord[] | null | undefined,
): Source {
  const nodes = uniqueById(nodesIn);
  const ticketTotal = uniqueById(ticketsIn).length;
  const tickets = uniqueById(ticketsIn).slice(0, TICKET_CAP);
  const sessions = uniqueById(sessionsIn);
  const nodeIds = new Set(nodes.map((node) => node.id));
  const ticketIds = new Set(tickets.map((ticket) => ticket.id));
  const seenDispatch = new Set<string>();
  const dispatches: Array<{ nodeId: string; ticketId: string }> = [];
  let hiddenDispatches = 0;
  for (const item of dispatchesIn ?? []) {
    if (!item.node_id || !item.ticket_id) continue;
    const key = `${item.node_id}|${item.ticket_id}`;
    if (seenDispatch.has(key)) continue;
    seenDispatch.add(key);
    if (!nodeIds.has(item.node_id) || !ticketIds.has(item.ticket_id)) {
      hiddenDispatches += 1;
      continue;
    }
    dispatches.push({ nodeId: item.node_id, ticketId: item.ticket_id });
  }
  return {
    nodes,
    tickets,
    sessions,
    dispatches,
    hiddenDispatches,
    ticketTotal,
    empty: nodes.length === 0 && tickets.length === 0 && sessions.length === 0,
  };
}

function signatureOf(data: Source): string {
  return [
    data.nodes.map((node) => node.id).sort().join(","),
    data.tickets.map((ticket) => ticket.id).sort().join(","),
    data.sessions.map((session) => `${session.id}@${session.node_id ?? ""}`).sort().join(","),
    data.dispatches.map((edge) => `${edge.nodeId}>${edge.ticketId}`).sort().join(","),
  ].join("#");
}

function labelsOf(data: Source): string {
  return [
    data.nodes.map((node) => `${node.id}|${node.name}|${node.host}|${node.status}`).join(","),
    data.tickets.map((ticket) => `${ticket.id}|${ticket.code}|${ticket.title}|${ticket.status}|${ticket.thread_id ?? ""}`).join(","),
    data.sessions
      .map((session) => `${session.id}|${session.turn_count}|${session.pinned}|${session.ticket_id ?? ""}`)
      .join(","),
  ].join(";");
}

function describeSelection(
  key: string | null,
  nodes: readonly NodeRecord[],
  tickets: readonly Ticket[],
  sessions: readonly SessionRecord[],
): string {
  if (!key) return "";
  const split = key.indexOf(":");
  const kind = key.slice(0, split);
  const id = key.slice(split + 1);
  if (kind === "node") {
    const node = nodes.find((item) => item.id === id);
    return node ? nodeTitle(node) : "";
  }
  if (kind === "session") {
    const session = sessions.find((item) => item.id === id);
    return session ? sessionTitle(session, nodes, tickets) : "";
  }
  return "";
}

function nodeTitle(node: NodeRecord): string {
  const name = node.name.trim() || node.id;
  const parts = [`节点 ${name}`, nodeTone(node.status).label];
  const host = node.host.trim();
  if (host) parts.push(host);
  if (name !== node.id) parts.push(node.id);
  return parts.join(" · ");
}

function ticketTitle(ticket: Ticket): string {
  const code = ticket.code.trim() || ticket.id;
  const parts = [`工单 ${code}`, ticketTone(ticket.status).label];
  const title = ticket.title.trim();
  if (title && title !== code) parts.push(title);
  return parts.join(" · ");
}

function sessionTitle(
  session: SessionRecord,
  nodes: readonly NodeRecord[],
  tickets: readonly Ticket[],
): string {
  const node = session.node_id ? nodes.find((item) => item.id === session.node_id) : undefined;
  let where = "未关联节点";
  if (session.node_id && node) where = `节点 ${node.name.trim() || node.id}`;
  else if (session.node_id) where = "节点不在图中";
  const turns = Number.isFinite(session.turn_count) ? session.turn_count : 0;
  const parts = [`会话 ${session.id}`, where, `${turns} 轮`];
  if (session.ticket_id) {
    const ticket = tickets.find((item) => item.id === session.ticket_id);
    parts.push(ticket ? `工单 ${ticket.code.trim() || ticket.id}` : "工单未收入本图");
  }
  if (session.pinned) parts.push("已固定");
  return parts.join(" · ");
}

function nodeTone(status: string): { label: string; color: string; off: boolean } {
  const value = status.trim().toLowerCase();
  if (value === "online") return { label: "在线", color: "var(--cyan)", off: false };
  if (value === "offline") return { label: "离线", color: "var(--bad)", off: true };
  return { label: "未知", color: "var(--muted)", off: true };
}

function ticketTone(status: string): { label: string; color: string } {
  switch (status.trim().toLowerCase()) {
    case "review":
      return { label: "待验收", color: "var(--violet)" };
    case "staged":
      return { label: "待派", color: "var(--amber)" };
    case "running":
      return { label: "进行中", color: "var(--blue)" };
    case "done":
      return { label: "完成", color: "var(--ok)" };
    case "rejected":
      return { label: "驳回", color: "var(--bad)" };
    case "assigned":
      return { label: "已派", color: "var(--cyan)" };
    case "pending":
      return { label: "待处理", color: "var(--amber)" };
    case "failed":
    case "error":
      return { label: "失败", color: "var(--bad)" };
    default:
      return { label: "其他", color: "var(--muted)" };
  }
}

function uniqueById<T extends { id: string }>(rows: readonly T[] | null | undefined): T[] {
  const seen = new Set<string>();
  const out: T[] = [];
  for (const row of rows ?? []) {
    if (!row || typeof row.id !== "string" || row.id.length === 0 || seen.has(row.id)) continue;
    seen.add(row.id);
    out.push(row);
  }
  return out;
}

function sessionAngle(key: string): number {
  // +y 向下。角度落在上半弧，躲开节点下方的文字。
  return Math.PI + unit(key) * Math.PI;
}

function initRank(kind: Kind): number {
  if (kind === "node") return 0;
  if (kind === "ticket") return 1;
  return 2;
}

function kindRank(kind: Kind): number {
  if (kind === "session") return 0;
  if (kind === "ticket") return 1;
  return 2;
}

function clip(text: string, max: number): string {
  const chars = Array.from(text.trim());
  if (chars.length <= max) return chars.join("");
  return `${chars.slice(0, Math.max(1, max - 1)).join("")}…`;
}

function unit(text: string): number {
  let hash = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) / 4294967295;
}

function round1(value: number): number {
  return Math.round(value * 10) / 10;
}

function clamp(value: number, min: number, max: number): number {
  if (max < min) return (min + max) / 2;
  return Math.max(min, Math.min(max, value));
}
</script>

<template>
  <EmptyState v-if="empty" title="暂无拓扑数据" />
  <div v-else class="board">
    <div ref="mesh" class="mesh" :style="{ height: `${MESH_H}px` }">
      <p v-if="info" class="info" role="status">{{ info }}</p>
      <svg
        v-if="scene.w > 0"
        class="mesh__svg"
        :viewBox="`0 0 ${scene.w} ${scene.h}`"
        :width="scene.w"
        :height="scene.h"
        role="group"
        aria-label="拓扑图"
        @keydown.esc.prevent="clearSelection"
      >
        <rect class="backdrop" :width="scene.w" :height="scene.h" @click="clearSelection" />
        <path
          v-for="edge in scene.edges"
          :key="edge.key"
          class="edge edge--dispatch"
          :d="edge.d"
        />
        <g
          v-for="glyph in scene.glyphs"
          :key="glyph.key"
          class="glyph"
          :class="[
            `glyph--${glyph.kind}`,
            { 'is-off': glyph.off, 'is-selected': selectedKey === glyph.key },
          ]"
          :style="{ color: glyph.color }"
          :transform="`translate(${glyph.x} ${glyph.y})`"
          role="button"
          tabindex="0"
          :aria-label="glyph.title"
          @click.stop="pick(glyph)"
          @keydown.enter.prevent.stop="pick(glyph)"
          @keydown.space.prevent.stop="pick(glyph)"
        >
          <title>{{ glyph.title }}</title>
          <path
            v-if="glyph.kind === 'node'"
            class="hex"
            d="M-11 -6.5 L0 -13 L11 -6.5 L11 6.5 L0 13 L-11 6.5 Z"
          />
          <rect v-else-if="glyph.kind === 'ticket'" class="box" x="-8" y="-8" width="16" height="16" />
          <template v-else>
            <circle class="hit" r="10" />
            <circle class="dot" r="4.2" />
          </template>
          <circle v-if="selectedKey === glyph.key" class="ring" :r="glyph.kind === 'session' ? 9 : 16" />
          <text v-if="glyph.label" class="nlabel" y="24" text-anchor="middle">{{ glyph.label }}</text>
          <text v-if="glyph.sub" class="nsub" y="36" text-anchor="middle">{{ glyph.sub }}</text>
        </g>
      </svg>
    </div>
    <div class="legend">
      <span class="legend__item legend__item--cyan">
        <svg class="legend__icon" width="18" height="16" viewBox="-12 -14 24 28" aria-hidden="true">
          <path class="hex" d="M-11 -6.5 L0 -13 L11 -6.5 L11 6.5 L0 13 L-11 6.5 Z" />
        </svg>
        节点 · 在线
      </span>
      <span class="legend__item legend__item--bad">
        <svg class="legend__icon" width="18" height="16" viewBox="-12 -14 24 28" aria-hidden="true">
          <path class="hex" d="M-11 -6.5 L0 -13 L11 -6.5 L11 6.5 L0 13 L-11 6.5 Z" />
        </svg>
        离线
      </span>
      <span class="legend__item legend__item--ticket">
        <svg class="legend__icon" width="16" height="16" viewBox="-10 -10 20 20" aria-hidden="true">
          <rect class="box" x="-8" y="-8" width="16" height="16" />
        </svg>
        工单
      </span>
      <span class="legend__item legend__item--cyan">
        <svg class="legend__icon" width="16" height="16" viewBox="-8 -8 16 16" aria-hidden="true">
          <circle class="dot" r="4.2" />
        </svg>
        会话
      </span>
      <span class="legend__item legend__item--cyan">
        <svg class="legend__icon legend__icon--line" width="28" height="8" viewBox="0 0 28 8" aria-hidden="true">
          <line class="edge edge--dispatch" x1="0" y1="4" x2="28" y2="4" />
        </svg>
        实线派单 · 节点到工单
      </span>
      <p class="legend__hint">点击工单进入工单列表，点击节点或会话查看信息</p>
    </div>
    <p v-if="footnote" class="note">{{ footnote }}</p>
  </div>
</template>

<style scoped>
.board {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  border: 1px solid var(--line);
  background:
    radial-gradient(70% 80% at 50% 42%, rgba(79, 216, 235, 0.05), transparent 70%),
    var(--bg);
}

.mesh {
  position: relative;
  width: 100%;
  max-width: 100%;
  min-width: 0;
  overflow: hidden;
}

.mesh__svg {
  display: block;
  width: 100%;
  max-width: 100%;
  height: 100%;
  overflow: hidden;
}

.backdrop {
  fill: transparent;
}

.edge {
  fill: none;
  stroke-width: 1.15;
  pointer-events: none;
}

.edge--dispatch {
  stroke: var(--cyan);
  stroke-opacity: 0.55;
}

.glyph {
  cursor: pointer;
  pointer-events: bounding-box;
}

.glyph:focus {
  outline: none;
}

.glyph--node:not(.is-off):not(:hover):not(.is-selected) {
  filter: drop-shadow(0 0 4px var(--cyan));
}

.glyph--ticket:not(:hover):not(.is-selected) {
  filter: drop-shadow(0 0 3px color-mix(in srgb, currentColor 70%, transparent));
}

.glyph--session:not(.is-off):not(:hover):not(.is-selected) {
  filter: drop-shadow(0 0 3px var(--cyan));
}

.glyph:hover,
.glyph:focus-visible,
.glyph.is-selected {
  filter: drop-shadow(0 0 8px currentColor);
}

.hex,
.box,
.dot {
  fill: currentColor;
  stroke: currentColor;
  stroke-width: 1.4;
}

.hex,
.box {
  fill-opacity: 0.14;
}

.dot {
  fill-opacity: 0.4;
  stroke-width: 1.3;
}

.hit,
.ring,
.nlabel,
.nsub {
  pointer-events: none;
}

.hit {
  fill: transparent;
}

.ring {
  fill: none;
  stroke: currentColor;
  stroke-width: 1;
  stroke-dasharray: 2 2;
}

.nlabel,
.nsub {
  font-family: var(--mono, ui-monospace, monospace);
  text-anchor: middle;
}

.nlabel {
  fill: var(--text);
  font-size: 11px;
}

.nsub {
  fill: currentColor;
  font-size: 10px;
}

.info {
  position: absolute;
  z-index: 1;
  top: 8px;
  right: 12px;
  left: 12px;
  margin: 0;
  overflow: hidden;
  color: var(--text);
  font-size: 12px;
  letter-spacing: 0.02em;
  white-space: nowrap;
  text-overflow: ellipsis;
  text-shadow: 0 0 8px rgba(4, 6, 14, 0.95);
  pointer-events: none;
}

.legend,
.note {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 16px;
  max-width: 100%;
  min-width: 0;
  margin: 0;
  padding: 8px 12px 10px;
  color: var(--muted);
  font-size: 12px;
}

.note {
  display: block;
  padding-top: 0;
  overflow-wrap: anywhere;
}

.legend__item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  white-space: nowrap;
}

.legend__item--cyan {
  color: var(--cyan);
}

.legend__item--bad {
  color: var(--bad);
}

.legend__item--violet {
  color: var(--violet);
}

.legend__item--ticket {
  color: var(--text);
}

.legend__icon {
  display: block;
  flex: 0 0 auto;
  overflow: visible;
}

.legend__icon--line {
  color: inherit;
}

.legend__hint {
  margin: 0;
  color: var(--muted);
}
</style>
