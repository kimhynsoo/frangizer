<script setup>
import { ref, watch, onUnmounted } from "vue";
import { ArrowUpRight, BookOpen, Volume2 } from "@lucide/vue";
import { speakLocally } from "../api/voice";
const props = defineProps({
  result: Object,
  question: { type: String, default: "" },
  disableSpeech: { type: Boolean, default: false },
});
defineEmits(["source"]);
const copied = ref(false),
  notice = ref(""),
  reading = ref(false);
let speech = null;
async function copy() {
  try {
    await navigator.clipboard.writeText(
      `문의: ${props.question}\n확인 내용: ${props.result.answer}\n검토 사유: ${props.result.handoff_reason || ""}\n추가 확인: ${(props.result.follow_up_questions || []).join(" / ")}`,
    );
    copied.value = true;
  } catch {
    notice.value = "복사 권한이 없습니다. 내용을 선택해 직접 복사해 주세요.";
  }
}
async function read() {
  if (props.disableSpeech) return;
  if (reading.value) {
    speech?.abort();
    reading.value = false;
    return;
  }
  speech = new AbortController();
  reading.value = true;
  const result = props.result;
  const prefix =
    result.status === "answered"
      ? "등록된 문서 기준입니다."
      : result.status === "handoff"
        ? "담당자 확인이 필요합니다."
        : "추가 확인이 필요합니다.";
  const ok = await speakLocally(
    `${prefix} ${result.basis !== "current" ? "과거 또는 예정 기준 답변입니다. " : ""}${result.answer} ${(result.steps || []).join(" ")} ${result.handoff_reason || ""}`,
    { signal: speech.signal },
  );
  if (!ok)
    notice.value =
      "기기에 설치된 한국어 읽기 음성이 없습니다. 화면의 답변을 확인해 주세요.";
  reading.value = false;
}
onUnmounted(() => speech?.abort());
watch(
  () => props.disableSpeech,
  (disabled) => {
    if (disabled) {
      speech?.abort();
      reading.value = false;
    }
  },
);
</script>
<template>
  <div class="answer-details">
    <p v-if="result.basis !== 'current'" class="info-banner">
      {{ result.basis === "future" ? "예정 기준" : "과거 기준" }} ·
      {{
        new Date(result.as_of).toLocaleString("ko-KR", {
          timeZone: "Asia/Seoul",
        })
      }}
    </p>
    <p class="answer-text">{{ result.answer }}</p>
    <button class="button ghost small" :disabled="disableSpeech" @click="read">
      <Volume2 :size="16" />{{ reading ? "읽기 중지" : "답변 읽어주기" }}
    </button>
    <ol v-if="result.steps?.length" class="answer-steps">
      <li v-for="(step, i) in result.steps" :key="i">{{ step }}</li>
    </ol>
    <div v-if="result.sources?.length" class="answer-sources">
      <div class="section-label">
        <BookOpen :size="16" />답변의 근거
        <span>{{ result.sources.length }}</span>
      </div>
      <button
        v-for="item in result.sources"
        :key="item.chunk_id"
        class="source-card"
        @click="$emit('source', item)"
      >
        <div>
          <span
            class="badge"
            :class="
              item.is_demo || item.material_type === 'synthetic'
                ? 'synthetic'
                : ''
            "
            >{{
              item.is_demo || item.material_type === "synthetic"
                ? "합성·시연 자료"
                : item.material_type === "official"
                  ? "법령·고시"
                  : "정보공개서 원본(수집본)"
            }}</span
          ><span class="muted">v{{ item.version }}</span>
        </div>
        <strong>{{ item.title }}<ArrowUpRight :size="16" /></strong
        ><small>{{ item.location }}</small>
        <p>{{ item.excerpt }}</p>
        <small
          >적용 시작
          {{
            new Date(item.effective_from).toLocaleDateString("ko-KR", {
              timeZone: "Asia/Seoul",
            })
          }}</small
        >
      </button>
    </div>
    <div v-if="result.follow_up_questions?.length" class="follow-up">
      <h4>추가로 확인해 주세요</h4>
      <ul>
        <li v-for="(item, i) in result.follow_up_questions" :key="i">
          {{ item }}
        </li>
      </ul>
    </div>
    <div
      v-if="result.status === 'handoff' || result.handoff_reason"
      class="handoff-box"
    >
      <p>{{ result.handoff_reason }}</p>
      <button class="button small secondary" @click="copy">
        {{ copied ? "복사됨" : "문의 요약 복사" }}</button
      ><small
        >담당자에게 직접 전달해 주세요. 이 화면에서 문의가 접수되지는
        않습니다.</small
      >
    </div>
    <p v-if="notice" class="muted" role="status">{{ notice }}</p>
  </div>
</template>
