<script setup>
defineProps({ meta: Object, busy: Boolean });
defineEmits(["change"]);
</script>
<template>
  <div v-if="meta" class="pagination">
    <span>총 {{ meta.total }}건</span>
    <div>
      <button
        class="button small ghost"
        :disabled="busy || meta.page <= 1"
        @click="$emit('change', meta.page - 1)"
      >
        이전</button
      ><span>{{ meta.page }} / {{ Math.max(1, meta.total_pages) }}</span
      ><button
        class="button small ghost"
        :disabled="busy || meta.page >= meta.total_pages"
        @click="$emit('change', meta.page + 1)"
      >
        다음
      </button>
    </div>
  </div>
</template>
