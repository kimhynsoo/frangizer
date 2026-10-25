<script setup>
defineProps({ error: Object });
defineEmits(["retry"]);
</script>
<template>
  <div v-if="error" class="error-notice" role="alert">
    <strong>{{ error.message }}</strong>
    <p v-if="error.nextAction">{{ error.nextAction }}</p>
    <small v-if="error.errorCode">오류 코드 · {{ error.errorCode }}</small>
    <small v-if="error.jobId">작업 ID · {{ error.jobId }}</small>
    <p v-if="error.retryAfter">
      {{ error.retryAfter }}초 후 다시 시도해 주세요
    </p>
    <p v-for="(field, i) in error.details || []" :key="i">
      {{ field.field }}: {{ field.message }}
    </p>
    <small v-if="error.requestId">요청 ID · {{ error.requestId }}</small
    ><button
      v-if="error.retryable"
      class="button small"
      @click="$emit('retry')"
    >
      다시 시도
    </button>
  </div>
</template>
