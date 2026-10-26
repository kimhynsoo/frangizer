<script setup>
defineProps({ size: { type: Number, default: 24 } });
</script>
<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.7"
    stroke-linecap="round"
    role="img"
    stroke-linejoin="round"
    aria-label="본사와 가맹점을 수평으로 연결하는 프랜자이저 심볼"
  >
    <rect x="2.25" y="6.25" width="7.5" height="11.5" rx="3.25" />
    <rect x="14.25" y="6.25" width="7.5" height="11.5" rx="3.25" />
    <path d="M8.25 12h7.5" />
    <circle cx="8.25" cy="12" r="1.45" fill="currentColor" stroke="none" />
    <circle cx="15.75" cy="12" r="1.45" fill="currentColor" stroke="none" />
  </svg>
</template>
