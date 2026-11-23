<script setup lang="ts">
import type {
  Dispatch,
  NodeRecord,
  SessionRecord,
  Ticket,
} from "../api";
import MeshGraph from "./MeshGraph.vue";

defineProps<{
  nodes: NodeRecord[];
  tickets: Ticket[];
  dispatches: Dispatch[];
  sessions: SessionRecord[];
}>();
</script>

<template>
  <MeshGraph
    :nodes="nodes"
    :tickets="tickets"
    :dispatches="dispatches"
    :sessions="sessions"
  />
</template>
