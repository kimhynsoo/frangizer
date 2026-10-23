<script setup>
defineProps({ status: String });
const names = {
  current: "현재 적용",
  scheduled: "적용 예정",
  not_applied: "미적용",
  not_current: "현재 적용 아님",
  application_unknown: "적용 기준 확인 필요",
  classification_failed: "분류 실패",
  pending: "분류 대기",
  classified: "분류 완료",
  corrected: "분류 수정됨",
  answered: "근거 확인",
  needs_review: "추가 확인 필요",
  handoff: "담당자 확인 필요",
  processing: "답변 중",
  system_error: "시스템 오류",
  draft: "초안",
  approved: "승인됨",
  published: "배포됨",
  not_started: "준비 전",
  queued: "검색 준비 대기",
  running: "검색 준비 중",
  ready: "검색 준비 완료",
  failed: "검색 준비 실패",
  unknown: "미확인",
  open: "접수",
  in_progress: "처리 중",
  closed: "종결",
};
</script>
<template>
  <span class="badge" :class="status">{{ names[status] || status }}</span>
</template>
