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
    stroke-width="1.5"
    stroke-linecap="round"
    role="img"
    stroke-linejoin="round"
    aria-label="저항 기호가 있는 선으로 수평 연결된 두 사람"
  >
    <circle cx="4" cy="6" r="2.5" />
    <rect x="1" y="11" width="6" height="9" rx="0.5" />
    <path d="M7 15.5h1l1-2.5 2 5 2-5 2 5 1-2.5h1" />
    <circle cx="20" cy="6" r="2.5" />
    <rect x="17" y="11" width="6" height="9" rx="0.5" />
  </svg>
</template>
