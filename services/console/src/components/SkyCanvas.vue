<script setup lang="ts">
// 星野 + 程序燕子 V 形编队。落地页横穿，传入 landing=false 时改为上方巡航。
import { onBeforeUnmount, onMounted, ref } from "vue";

const props = withDefaults(
  defineProps<{
    landing?: boolean;
  }>(),
  { landing: true },
);

interface Star {
  x: number;
  y: number;
  r: number;
  p: number;
  s: number;
}

interface Bird {
  ox: number;
  oy: number;
  s: number;
  p: number;
}

const canvasRef = ref<HTMLCanvasElement | null>(null);
let ctx: CanvasRenderingContext2D | null = null;
let raf = 0;
let width = 0;
let height = 0;
let stars: Star[] = [];
const t0 = performance.now();

const flock: Bird[] = [
  { ox: 0, oy: 0, s: 1.25, p: 0 },
  ...Array.from({ length: 10 }, (_, i): Bird => {
    const side = i % 2 ? 1 : -1;
    const rank = Math.floor(i / 2) + 1;
    return {
      ox: -rank * 34,
      oy: side * rank * 22,
      s: 1.05 - rank * 0.04,
      p: Math.random() * 6.28,
    };
  }),
];

function resize() {
  const canvas = canvasRef.value;
  if (!canvas) return;
  const ratio = window.devicePixelRatio || 1;
  width = canvas.width = window.innerWidth * ratio;
  height = canvas.height = window.innerHeight * ratio;
  canvas.style.width = `${window.innerWidth}px`;
  canvas.style.height = `${window.innerHeight}px`;
  stars = Array.from({ length: 150 }, () => ({
    x: Math.random() * width,
    y: Math.random() * height,
    r: Math.random() * 1.4 + 0.3,
    p: Math.random() * 6.28,
    s: 0.5 + Math.random() * 1.5,
  }));
}

function swallow(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  ang: number,
  flap: number,
  size: number,
  alpha: number,
  hue: string,
) {
  const ratio = window.devicePixelRatio || 1;
  context.save();
  context.translate(x, y);
  context.rotate(ang);
  context.scale(size * ratio, size * ratio);
  context.globalAlpha = alpha;
  context.strokeStyle = hue;
  context.lineWidth = 1.5;
  context.lineCap = "round";
  context.shadowColor = hue;
  context.shadowBlur = 9;
  const wing = Math.sin(flap) * 7;
  context.beginPath();
  context.moveTo(7, 0);
  context.quadraticCurveTo(0, -1.6, -6, 0);
  context.moveTo(-6, 0);
  context.lineTo(-10, -2.4);
  context.moveTo(-6, 0);
  context.lineTo(-10, 2.4);
  context.stroke();
  context.beginPath();
  context.moveTo(1, 0);
  context.quadraticCurveTo(-2, -6 - wing, -8, -9 - wing * 1.4);
  context.moveTo(1, 0);
  context.quadraticCurveTo(-2, 6 + wing, -8, 9 + wing * 1.4);
  context.stroke();
  context.restore();
}

function frame(now: number) {
  raf = requestAnimationFrame(frame);
  const context = ctx;
  if (!context) return;
  const t = (now - t0) / 1000;
  context.clearRect(0, 0, width, height);
  const ratio = window.devicePixelRatio || 1;
  for (const star of stars) {
    context.globalAlpha = 0.25 + 0.5 * Math.abs(Math.sin(t * star.s + star.p));
    context.fillStyle = "#9fb4ff";
    context.beginPath();
    context.arc(star.x, star.y, star.r * ratio, 0, 6.28);
    context.fill();
  }
  context.globalAlpha = 1;
  const landing = props.landing;
  let lx: number;
  let ly: number;
  let ang: number;
  if (landing) {
    const c = (t % 26) / 26;
    lx = (-0.12 + c * 1.24) * width;
    // 从标题上方掠过，避免压在字上影响阅读
    ly = height * (0.16 + 0.06 * Math.sin(c * 6.28 * 1.5));
    ang = Math.atan2(
      ((0.06 * 6.28 * 1.5 * Math.cos(c * 6.28 * 1.5) * height) / 26),
      (1.24 * width) / 26,
    );
  } else {
    lx = width * (0.72 + 0.16 * Math.cos(t * 0.11));
    ly = height * (0.16 + 0.07 * Math.sin(t * 0.17));
    ang = Math.atan2(0.07 * 0.17 * Math.cos(t * 0.17), -0.16 * 0.11 * Math.sin(t * 0.11));
  }
  const scale = landing ? 1.5 : 0.8;
  const alpha = landing ? 0.95 : 0.4;
  for (const bird of flock) {
    const jx = Math.sin(t * 1.3 + bird.p) * 3;
    const jy = Math.cos(t * 1.7 + bird.p) * 2.4;
    swallow(
      context,
      lx + (bird.ox + jx) * scale * ratio,
      ly + (bird.oy + jy) * scale * ratio,
      ang,
      t * 7 + bird.p,
      bird.s * scale,
      alpha,
      bird.ox === 0 ? "#f5b942" : "#bfe9f5",
    );
  }
}

function onResize() {
  resize();
}

onMounted(() => {
  const canvas = canvasRef.value;
  if (!canvas) return;
  ctx = canvas.getContext("2d");
  if (!ctx) return;
  resize();
  window.addEventListener("resize", onResize);
  raf = requestAnimationFrame(frame);
});

onBeforeUnmount(() => {
  cancelAnimationFrame(raf);
  window.removeEventListener("resize", onResize);
});
</script>

<template>
  <canvas ref="canvasRef" class="sky" aria-hidden="true"></canvas>
</template>

<style scoped>
.sky {
  position: fixed;
  inset: 0;
  z-index: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}
</style>
