<script setup lang="ts">
// 阵眼：把节点、会话、工单画成一张实时极坐标阵图。
// 所有图形都来自真实数据：在线节点、在途派工、待验收工单。
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { Dispatch, NodeRecord, SessionRecord, Ticket } from "../api";
import CountUp from "./CountUp.vue";

const props = defineProps<{
  nodes: NodeRecord[];
  sessions: SessionRecord[];
  tickets: Ticket[];
  dispatches: Dispatch[];
  review: number;
  stalled: number;
  running: number;
  delivered: number;
}>();

interface Dot {
  id: string;
  angle: number;
  radius: number;
  size: number;
  tone: string;
  diamond: boolean;
  pulse: boolean;
  label?: string;
}

interface Edge {
  from: number;
  to: number;
  tone: string;
}

const canvasRef = ref<HTMLCanvasElement | null>(null);
const reduceMotion =
  typeof window !== "undefined" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const onlineNodes = computed(
  () => props.nodes.filter((node) => node.status === "online").length,
);
const activeDispatches = computed(
  () =>
    props.dispatches.filter((item) =>
      ["dispatched", "created", "pending"].includes(item.state),
    ).length,
);

let raf = 0;
let ctx: CanvasRenderingContext2D | null = null;
let width = 0;
let height = 0;
let dpr = 1;
const t0 = performance.now();

const stars = Array.from({ length: 90 }, () => ({
  x: Math.random(),
  y: Math.random(),
  r: Math.random() * 1.2 + 0.25,
  p: Math.random() * 6.28,
  s: 0.4 + Math.random() * 1.2,
}));

function layout(): { dots: Dot[]; edges: Edge[]; center: number; ring: number } {
  const cx = width / 2;
  const R = Math.min(width, height) * 0.44;
  const dots: Dot[] = [];
  const edges: Edge[] = [];
  const nodeIndex = new Map<string, number>();

  const nodes = props.nodes.slice(0, 12);
  if (nodes.length) {
    nodes.forEach((node, i) => {
      const angle = -Math.PI / 2 + (i / nodes.length) * Math.PI * 2;
      nodeIndex.set(node.id, dots.length);
      dots.push({
        id: `node-${node.id}`,
        angle,
        radius: 0.6,
        size: 3.2 + Math.min(node.max_workers || 1, 6) * 0.35,
        tone: node.status === "online" ? "#4fd8eb" : "#f26d7d",
        diamond: true,
        pulse: node.status === "online",
        label: node.name || node.id,
      });
    });
    // 控制面与节点之间的引力线
    for (let i = 0; i < nodes.length; i += 1) {
      edges.push({ from: -1, to: i, tone: "cyan" });
    }
    nodes.forEach((node, i) => {
      const peers = props.sessions.filter((s) => s.node_id === node.id).slice(0, 3);
      peers.forEach((session, j) => {
        nodeIndex.set(session.id, dots.length);
        dots.push({
          id: `session-${session.id}`,
          angle: -Math.PI / 2 + (i / nodes.length) * Math.PI * 2 + (j - 1) * 0.16,
          radius: 0.38 + j * 0.05,
          size: 1.8,
          tone: "#b18cff",
          diamond: false,
          pulse: false,
        });
        edges.push({ from: dots.length - 1, to: i, tone: "violet" });
      });
    });
  } else {
    for (let i = 0; i < 6; i += 1) {
      dots.push({
        id: `ghost-${i}`,
        angle: -Math.PI / 2 + (i / 6) * Math.PI * 2,
        radius: 0.6,
        size: 2.4,
        tone: "#3a4568",
        diamond: true,
        pulse: false,
      });
    }
  }

  // 外层：还在系统里的工单。待验收最亮，进行中次之。
  const tickets = props.tickets
    .filter((ticket) => ["review", "staged", "assigned", "running"].includes(ticket.status))
    .slice(0, 18);
  const offset = Math.PI / 7;
  tickets.forEach((ticket, i) => {
    const angle = offset + (i / Math.max(tickets.length, 1)) * Math.PI * 2;
    const review = ticket.status === "review";
    dots.push({
      id: `ticket-${ticket.id}`,
      angle,
      radius: 0.88,
      size: review ? 2.8 : 2,
      tone: review ? "#f5b942" : ticket.status === "running" ? "#4fd8eb" : "#5f6b96",
      diamond: false,
      pulse: review,
    });
    for (const dispatch of props.dispatches) {
      if (dispatch.ticket_id !== ticket.id) continue;
      const to = nodeIndex.get(dispatch.node_id);
      if (to === undefined) continue;
      edges.push({ from: dots.length - 1, to, tone: "amber" });
    }
  });

  return { dots, edges, center: cx, ring: R };
}

const palette: Record<string, string> = {
  cyan: "#4fd8eb",
  violet: "#b18cff",
  amber: "#f5b942",
};

function point(angle: number, radius: number, cx: number, cy: number, R: number) {
  return { x: cx + Math.cos(angle) * radius * R, y: cy + Math.sin(angle) * radius * R };
}

function draw(now: number) {
  const context = ctx;
  if (!context) return;
  const t = (now - t0) / 1000;
  const { dots, edges, center, ring: R } = layout();
  const cy = height / 2;

  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  context.clearRect(0, 0, width, height);

  for (const star of stars) {
    context.globalAlpha = 0.18 + 0.35 * Math.abs(Math.sin(t * star.s + star.p));
    context.fillStyle = "#9fb4ff";
    context.beginPath();
    context.arc(star.x * width, star.y * height, star.r, 0, 6.28);
    context.fill();
  }

  // 同心环
  context.globalAlpha = 0.1;
  context.strokeStyle = "#4fd8eb";
  context.lineWidth = 1;
  context.beginPath();
  context.moveTo(center - R, cy);
  context.lineTo(center + R, cy);
  context.moveTo(center, cy - R);
  context.lineTo(center, cy + R);
  context.stroke();

  context.globalAlpha = 1;
  for (const ratio of [0.38, 0.6, 0.88]) {
    context.strokeStyle = "rgba(79, 216, 235, 0.14)";
    context.lineWidth = 1;
    context.beginPath();
    context.arc(center, cy, R * ratio, 0, 6.28);
    context.stroke();
  }

  // 外圈刻度
  context.strokeStyle = "rgba(79, 216, 235, 0.28)";
  for (let i = 0; i < 48; i += 1) {
    const angle = (i / 48) * Math.PI * 2;
    const long = i % 4 === 0;
    const r1 = R * 0.95;
    const r2 = R * (long ? 0.9 : 0.93);
    context.globalAlpha = long ? 0.5 : 0.22;
    context.beginPath();
    context.moveTo(center + Math.cos(angle) * r1, cy + Math.sin(angle) * r1);
    context.lineTo(center + Math.cos(angle) * r2, cy + Math.sin(angle) * r2);
    context.stroke();
  }
  context.globalAlpha = 1;

  // 扫描扇区
  const sweep = t * 0.55;
  for (let i = 0; i < 24; i += 1) {
    const angle = sweep - i * 0.035;
    context.globalAlpha = 0.12 * (1 - i / 24);
    context.strokeStyle = "#4fd8eb";
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(center, cy);
    context.lineTo(center + Math.cos(angle) * R * 0.95, cy + Math.sin(angle) * R * 0.95);
    context.stroke();
  }
  context.globalAlpha = 1;

  // 引力线
  const centerPoint = { x: center, y: cy };
  for (const edge of edges) {
    const to = dots[edge.to];
    if (!to) continue;
    const from =
      edge.from < 0 ? centerPoint : point(dots[edge.from].angle, dots[edge.from].radius, center, cy, R);
    const target = point(to.angle, to.radius, center, cy, R);
    const mx = (from.x + target.x) / 2;
    const my = (from.y + target.y) / 2;
    const bow = edge.from < 0 ? 0 : 0.12;
    const nx = mx + (my - cy) * bow;
    const ny = my - (mx - center) * bow;
    const tone = palette[edge.tone] ?? "#4fd8eb";
    context.globalAlpha = edge.from < 0 ? 0.12 : 0.22;
    context.strokeStyle = tone;
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(from.x, from.y);
    context.quadraticCurveTo(nx, ny, target.x, target.y);
    context.stroke();

    if (!reduceMotion) {
      const phase = (t * 0.35 + target.x * 0.01 + target.y * 0.013) % 1;
      const p = bezier(from, { x: nx, y: ny }, target, phase);
      context.globalAlpha = 0.85;
      context.fillStyle = tone;
      context.beginPath();
      context.arc(p.x, p.y, edge.from < 0 ? 1.3 : 1.7, 0, 6.28);
      context.fill();
    }
  }
  context.globalAlpha = 1;

  // 阵眼
  const core = reduceMotion ? 0 : t * 0.4;
  context.save();
  context.translate(center, cy);
  context.rotate(core);
  context.strokeStyle = "rgba(79, 216, 235, 0.75)";
  context.lineWidth = 1.2;
  context.shadowColor = "#4fd8eb";
  context.shadowBlur = 12;
  context.beginPath();
  context.rect(-6, -6, 12, 12);
  context.stroke();
  context.restore();
  context.shadowBlur = 0;
  context.fillStyle = "#eaf9ff";
  context.beginPath();
  context.arc(center, cy, 2.6, 0, 6.28);
  context.fill();

  // 节点 / 会话 / 工单
  for (const dot of dots) {
    const p = point(dot.angle, dot.radius, center, cy, R);
    context.globalAlpha = 0.95;
    context.strokeStyle = dot.tone;
    context.fillStyle = dot.tone;
    context.shadowColor = dot.tone;
    context.shadowBlur = 8;
    if (dot.diamond) {
      context.save();
      context.translate(p.x, p.y);
      context.rotate(Math.PI / 4);
      context.lineWidth = 1.2;
      context.strokeRect(-dot.size, -dot.size, dot.size * 2, dot.size * 2);
      context.restore();
    } else {
      context.beginPath();
      context.arc(p.x, p.y, dot.size, 0, 6.28);
      context.fill();
    }
    if (dot.pulse && !reduceMotion) {
      const phase = (((t * 0.8 + dot.angle) % 1) + 1) % 1;
      context.globalAlpha = (1 - phase) * 0.5;
      context.beginPath();
      context.arc(p.x, p.y, dot.size + phase * 9, 0, 6.28);
      context.stroke();
    }
    if (dot.label) {
      const lx = p.x + Math.cos(dot.angle) * 15;
      const ly = p.y + Math.sin(dot.angle) * 15;
      const cos = Math.cos(dot.angle);
      const sin = Math.sin(dot.angle);
      context.globalAlpha = 0.72;
      context.fillStyle = "#9fb4ff";
      context.font = "10px 'Cascadia Mono', 'Microsoft YaHei', monospace";
      context.textAlign = cos < -0.25 ? "right" : cos > 0.25 ? "left" : "center";
      context.textBaseline = sin < -0.25 ? "bottom" : sin > 0.25 ? "top" : "middle";
      context.fillText(dot.label, lx, ly);
    }
  }
  context.globalAlpha = 1;
  context.shadowBlur = 0;

  if (!reduceMotion) raf = requestAnimationFrame(draw);
}

function bezier(
  a: { x: number; y: number },
  c: { x: number; y: number },
  b: { x: number; y: number },
  t: number,
) {
  const u = 1 - t;
  return {
    x: u * u * a.x + 2 * u * t * c.x + t * t * b.x,
    y: u * u * a.y + 2 * u * t * c.y + t * t * b.y,
  };
}

function resize() {
  const canvas = canvasRef.value;
  const host = canvas?.parentElement;
  if (!canvas || !host) return;
  const rect = host.getBoundingClientRect();
  dpr = window.devicePixelRatio || 1;
  width = Math.max(1, rect.width);
  height = Math.max(1, rect.height);
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  ctx = canvas.getContext("2d");
  if (reduceMotion) draw(performance.now());
}

let observer: ResizeObserver | null = null;

onMounted(() => {
  const canvas = canvasRef.value;
  const host = canvas?.parentElement;
  if (!canvas || !host) return;
  resize();
  observer = new ResizeObserver(() => {
    cancelAnimationFrame(raf);
    resize();
    if (!reduceMotion) raf = requestAnimationFrame(draw);
  });
  observer.observe(host);
  if (!reduceMotion) raf = requestAnimationFrame(draw);
});

watch(
  () => [props.nodes, props.tickets, props.dispatches, props.sessions],
  () => {
    if (reduceMotion) draw(performance.now());
  },
);

onBeforeUnmount(() => {
  cancelAnimationFrame(raf);
  observer?.disconnect();
});
</script>

<template>
  <section class="deck" aria-label="阵眼总览">
    <div class="deck__metrics">
      <button type="button" class="tile tile--review" @click="$router.push('/tickets')">
        <span class="tile__label">待你验收</span>
        <b class="tile__value"><count-up :value="review" /></b>
        <span class="tile__hint">人审是硬闸，点开处理</span>
      </button>
      <button type="button" class="tile tile--stuck" @click="$router.push('/signals')">
        <span class="tile__label">卡住的事</span>
        <b class="tile__value"><count-up :value="stalled" /></b>
        <span class="tile__hint">超时、失败、节点离线</span>
      </button>
      <button type="button" class="tile tile--running" @click="$router.push('/fleet')">
        <span class="tile__label">正在跑</span>
        <b class="tile__value"><count-up :value="running" /></b>
        <span class="tile__hint">{{ onlineNodes }} / {{ nodes.length }} 节点在线</span>
      </button>
      <button type="button" class="tile tile--done" @click="$router.push('/tickets')">
        <span class="tile__label">已验收</span>
        <b class="tile__value"><count-up :value="delivered" /></b>
        <span class="tile__hint">累计通过</span>
      </button>
    </div>

    <div class="deck__radar">
      <canvas ref="canvasRef" class="deck__canvas" aria-hidden="true"></canvas>
      <span class="deck__tag">GRID / LIVE</span>
      <p class="deck__legend">
        <i class="lg lg--node"></i>节点
        <i class="lg lg--session"></i>会话
        <i class="lg lg--ticket"></i>待验收
        <i class="lg lg--active"></i>在途
      </p>
      <p class="deck__caption">
        {{ activeDispatches ? `${activeDispatches} 个派工在路上` : "暂无在途派工" }}
      </p>
    </div>
  </section>
</template>

<style scoped>
.deck {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(0, 1fr);
  gap: 12px;
  min-width: 0;
  margin-bottom: 12px;
  padding: 12px;
  background:
    linear-gradient(180deg, rgba(79, 216, 235, 0.05), transparent 42%),
    var(--bg-elevated);
  border: 1px solid var(--line);
}

.deck::before {
  content: "";
  position: absolute;
  top: -1px;
  left: -1px;
  width: 42px;
  height: 2px;
  background: var(--cyan);
  box-shadow: 0 0 10px var(--cyan);
}

.deck__metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  min-width: 0;
}

.tile {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  min-width: 0;
  padding: 12px 14px 10px;
  background: rgba(4, 6, 14, 0.55);
  border: 1px solid rgba(48, 58, 84, 0.7);
  color: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.18s, box-shadow 0.18s, transform 0.18s;
}

.tile::after {
  content: "";
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 2px;
  background: currentColor;
  opacity: 0.85;
}

.tile:hover,
.tile:focus-visible {
  border-color: currentColor;
  box-shadow: 0 0 18px -6px currentColor;
  transform: translateY(-1px);
  outline: none;
}

.tile__label {
  color: var(--muted);
  font-size: 12px;
  letter-spacing: 0.16em;
}

.tile__value {
  color: currentColor;
  font-family: var(--mono);
  font-size: 34px;
  line-height: 1;
  font-weight: 700;
  text-shadow: 0 0 16px currentColor;
}

.tile__hint {
  color: var(--muted);
  font-size: 11px;
}

.tile--review {
  color: var(--amber);
}

.tile--stuck {
  color: var(--bad);
}

.tile--running {
  color: var(--cyan);
}

.tile--done {
  color: var(--ok);
}

.deck__radar {
  position: relative;
  min-width: 0;
  height: 100%;
  min-height: 224px;
  border: 1px solid rgba(48, 58, 84, 0.55);
  background:
    radial-gradient(58% 58% at 50% 50%, rgba(79, 216, 235, 0.08), transparent 70%),
    rgba(2, 4, 10, 0.55);
  overflow: hidden;
}

.deck__canvas {
  position: absolute;
  inset: 0;
  display: block;
  width: 100%;
  height: 100%;
}

.deck__tag {
  position: absolute;
  top: 8px;
  right: 10px;
  color: rgba(79, 216, 235, 0.75);
  font-family: var(--mono);
  font-size: 10px;
  letter-spacing: 0.24em;
}

.deck__legend {
  position: absolute;
  bottom: 6px;
  left: 10px;
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0;
  color: var(--muted);
  font-size: 10px;
  letter-spacing: 0.08em;
}

.lg {
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-right: 4px;
  border-radius: 50%;
  vertical-align: 1px;
}

.lg--node {
  background: var(--cyan);
  box-shadow: 0 0 6px var(--cyan);
}

.lg--session {
  background: var(--violet);
}

.lg--ticket {
  background: var(--amber);
}

.lg--active {
  background: #5f6b96;
}

.deck__caption {
  position: absolute;
  right: 10px;
  bottom: 6px;
  margin: 0;
  color: var(--muted);
  font-size: 10px;
  letter-spacing: 0.08em;
}

@media (max-width: 900px) {
  .deck {
    grid-template-columns: minmax(0, 1fr);
  }

  .deck__radar {
    min-height: 180px;
  }
}

@media (max-width: 640px) {
  .deck__metrics {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
