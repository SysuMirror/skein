<script setup lang="ts">
import { useRouter } from "vue-router";

const router = useRouter();
</script>

<template>
  <section class="about">
    <header class="about__head">
      <h2>Skein 是什么</h2>
      <button type="button" class="ghost" @click="router.push('/today')">
        回到工作台 →
      </button>
    </header>

    <p class="about__lead">把真实用户反馈，变成可验收的修复。</p>
    <p class="about__sub">
      不是聊天机器人，也不是工单后台。Skein 从真实反馈出发，调度节点上的编码 Agent
      完成修复并交付分支，最后由人验收。
    </p>

    <section class="about__block">
      <h3>一条反馈会经历什么</h3>
      <ol class="flow">
        <li><b>采集</b><span>帖子、评论、对话等外部消息进入事件流。</span></li>
        <li><b>准入</b><span>程序先判断：忽略、只记账、还是处理。</span></li>
        <li><b>建单</b><span>同一线索归并，去重后生成工单。</span></li>
        <li><b>派工</b><span>派给节点上隔离的 Agent 会话执行。</span></li>
        <li><b>交付</b><span>分支、提交、diffstat、自测总结回到控制面，并开出草稿 PR。</span></li>
        <li><b>验收</b><span>人决定通过或驳回；系统不自行合入。</span></li>
      </ol>
    </section>

    <section class="about__block">
      <h3>三个工作面</h3>
      <dl class="areas">
        <dt>工作台</dt><dd>现在要我做什么：待验收、卡住的派工、正在跑的任务。</dd>
        <dt>需求</dt><dd>每张工单从哪来、谁在做、走到哪一步。</dd>
        <dt>运行</dt><dd>总览拓扑、节点、Worker 会话、事件流、信号。</dd>
      </dl>
    </section>

    <section class="about__block">
      <h3>不做什么</h3>
      <ul class="not">
        <li>不自行合入、不代替人做上线决定；分支和 PR 由人处理。</li>
        <li>不绑定某一种登录方式：不配 SSO 时用共享令牌，配了才走 SSO。</li>
        <li>不做监控大盘；指标只保留能直接指向动作的。</li>
      </ul>
    </section>

    <p class="about__foot">
      完整定义见仓库 <code>docs/architecture.md</code>。
    </p>
  </section>
</template>

<style scoped>
.about {
  max-width: 860px;
  min-width: 0;
}

.about__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line);
}

.about__head h2 {
  margin: 0;
  color: var(--text);
  font-size: 16px;
  letter-spacing: 0.14em;
}

.ghost {
  padding: 4px 10px 6px;
  background: transparent;
  border: none;
  color: var(--cyan);
  font-size: 12px;
  letter-spacing: 0.1em;
  cursor: pointer;
}

.about__lead {
  margin: 22px 0 6px;
  color: var(--text);
  font-size: 24px;
  font-weight: 700;
  letter-spacing: 0.06em;
}

.about__sub {
  max-width: 680px;
  margin: 0;
  color: var(--muted);
  font-size: 14px;
  line-height: 1.8;
}

.about__block {
  margin-top: 22px;
  padding-left: 14px;
  border-left: 1px solid rgba(79, 216, 235, 0.3);
}

.about__block h3 {
  margin: 0 0 8px;
  color: var(--cyan);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.16em;
}

.flow {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px 18px;
  margin: 0;
  padding: 0;
  list-style: none;
  counter-reset: flow;
}

@media (max-width: 900px) {
  .flow {
    grid-template-columns: minmax(0, 1fr);
  }
}

.flow li {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 8px 10px;
  background: var(--bg-elevated);
  border: 1px solid var(--line);
  counter-increment: flow;
}

.flow li::before {
  content: counter(flow, decimal-leading-zero);
  color: var(--cyan);
  font-family: var(--mono);
  font-size: 11px;
  letter-spacing: 0.1em;
}

.flow b {
  color: var(--text);
  font-size: 13px;
}

.flow span {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.6;
}

.areas {
  display: grid;
  grid-template-columns: 90px minmax(0, 1fr);
  gap: 7px 12px;
  margin: 0;
  font-size: 13px;
}

.areas dt {
  color: var(--text);
  font-weight: 600;
}

.areas dd {
  margin: 0;
  color: var(--muted);
  line-height: 1.6;
}

.not {
  margin: 0;
  padding-left: 18px;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.9;
}

.about__foot {
  margin-top: 22px;
  color: var(--muted);
  font-size: 11px;
}

code {
  font-family: var(--mono);
  color: var(--cyan);
}
</style>
