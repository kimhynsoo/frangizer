<script setup>
import { ref, computed, watch, onUnmounted } from "vue";
import { ArrowUpRight, BookOpen, Volume2 } from "@lucide/vue";
import { speakLocally } from "../api/voice";
const props = defineProps({
  result: Object,
  externalReading: { type: Boolean, default: null },
  question: { type: String, default: "" },
  disableSpeech: { type: Boolean, default: false },
  allowReadAloud: { type: Boolean, default: true },
});
const emit = defineEmits(["source", "toggleRead"]);
const notice = ref(""),
  reading = ref(false);
const isReading = computed(() => props.externalReading ?? reading.value);
let speech = null;
async function read() {
  if (props.disableSpeech || !props.allowReadAloud) return;
  if (props.externalReading !== null) {
    emit("toggleRead");
    return;
  }
  if (reading.value) {
    speech?.abort();
    reading.value = false;
    return;
  }
  speech = new AbortController();
  reading.value = true;
  const result = props.result;
  const ok = await speakLocally(
    `${result.answer} ${(result.steps || []).join(" ")}`,
    { signal: speech.signal },
  );
  if (!ok)
    notice.value =
      "기기에 설치된 한국어 읽기 음성이 없습니다. 화면의 답변을 확인해 주세요.";
  reading.value = false;
}
onUnmounted(() => speech?.abort());
watch(
  () => props.disableSpeech || !props.allowReadAloud,
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
    <button
      v-if="allowReadAloud"
      class="button ghost small"
      :disabled="disableSpeech"
      @click="read"
    >
      <Volume2 :size="16" />{{ isReading ? "읽기 중지" : "답변 읽어주기" }}
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
    <p v-if="notice" class="muted" role="status">{{ notice }}</p>
  </div>
</template>
