<script setup>
import { ref, useId } from "vue";
import { ChevronDown } from "@lucide/vue";
defineProps({
  name: { type: String, required: true },
  monogram: { type: String, default: "" },
  caption: { type: String, default: "브랜드 업무 공간" },
});
const expanded = ref(false);
const panelId = useId();
</script>
<template>
  <section class="brand-workspace" :aria-label="name">
    <button
      type="button"
      class="brand-switch"
      :aria-expanded="expanded"
      :aria-controls="panelId"
      @click="expanded = !expanded"
    >
      <span class="store-monogram" aria-hidden="true">{{
        monogram || name.slice(0, 1)
      }}</span>
      <span class="brand-switch-label"
        ><strong>{{ name }}</strong
        ><small>{{ caption }}</small></span
      >
      <ChevronDown
        :size="17"
        class="brand-chevron"
        :class="{ expanded }"
        aria-hidden="true"
      />
    </button>
    <div v-if="expanded" :id="panelId" class="brand-workspace-menu">
      <div class="nav-label">WORKSPACE</div>
      <nav :aria-label="`${name} 주요 메뉴`"><slot /></nav>
    </div>
  </section>
</template>
