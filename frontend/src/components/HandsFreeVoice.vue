<script setup>
import { ref, computed, watch, onUnmounted } from "vue";
import { Radio, Square } from "@lucide/vue";
import { recognitionAvailable } from "../api/voice";
import { createHandsFree } from "../api/hands-free";
import { loadWakeProfile } from "../api/wake-profile";
import WakeEnrollment from "./WakeEnrollment.vue";
const props = defineProps({
  ask: { type: Function, required: true },
  active: Boolean,
  profileKey: { type: String, default: "" },
  disabled: Boolean,
  allowReadAloud: { type: Boolean, default: true },
});
const emit = defineEmits(["enabled", "listening"]);
const enrollment = ref(false),
  aliases = ref(loadWakeProfile(props.profileKey)),
  phase = ref("off"),
  partial = ref(""),
  heard = ref(""),
  error = ref(""),
  notice = ref("");
const enabled = computed(() => phase.value !== "off");
const supported = recognitionAvailable() && globalThis.isSecureContext;
const labels = {
  waiting: "“시리”라고 불러 주세요",
  prompting: "네, 말씀하세요",
  question: "질문을 듣고 있어요 · 말이 끝나면 자동으로 전송합니다.",
  thinking: "답변을 기다리고 있어요",
  speaking: "답변을 읽고 있어요 · 다시 부르면 멈추고 새 질문을 받아요",
};
const session = createHandsFree({
  ask: (text) => props.ask(text),
  allowReadAloud: props.allowReadAloud,
  getAliases: () => aliases.value,
  onState: (state) => {
    phase.value = state;
    emit("enabled", state !== "off" || enrollment.value);
    emit("listening", ["waiting", "question", "speaking"].includes(state));
  },
  onPartial: (text) => (partial.value = text),
  onHeard: (text) => (heard.value = text),
  onError: (text) => (error.value = text),
  onNotice: (text) => (notice.value = text),
});
function toggle() {
  if (enabled.value) {
    session.stop();
    return;
  }
  if (!supported || props.disabled || !props.active) return;
  error.value = "";
  notice.value = "";
  heard.value = "";
  aliases.value = loadWakeProfile(props.profileKey);
  session.start();
}
function openEnrollment() {
  if (!supported || props.disabled || !props.active) return;
  session.stop();
  enrollment.value = true;
  emit("enabled", true);
}
function closeEnrollment() {
  enrollment.value = false;
  emit("enabled", false);
  emit("listening", false);
}
function stopAll() {
  session.stop();
  if (enrollment.value) closeEnrollment();
}
watch(
  () => props.active,
  (active) => {
    if (!active) stopAll();
  },
);
function hidden() {
  if (document.hidden) stopAll();
}
function escape(event) {
  if (event.key === "Escape") stopAll();
}
document.addEventListener("visibilitychange", hidden);
document.addEventListener("keydown", escape);
window.addEventListener("pagehide", stopAll);
onUnmounted(() => {
  stopAll();
  document.removeEventListener("visibilitychange", hidden);
  document.removeEventListener("keydown", escape);
  window.removeEventListener("pagehide", stopAll);
});
defineExpose({ stop: stopAll });
</script>
<template>
  <div class="hands-free" :class="{ enabled }">
    <div class="hands-free-row">
      <button
        type="button"
        class="button small secondary"
        :aria-pressed="enabled"
        :disabled="!enabled && (!supported || disabled)"
        @click="toggle"
      >
        <component :is="enabled ? Square : Radio" :size="16" />{{
          enabled ? "음성 대기 끄기" : "음성 대기 켜기"
        }}
      </button>
      <button
        v-if="supported && profileKey && !enabled"
        type="button"
        class="button ghost small"
        :disabled="disabled || enrollment"
        @click="openEnrollment"
      >
        {{ aliases.length ? "내 발음 재등록" : "내 발음 등록" }}
      </button>
      <span v-if="enabled" class="hands-free-status" role="status">{{
        labels[phase]
      }}</span>
    </div>
    <p
      v-if="enabled && phase === 'waiting' && heard"
      class="hands-free-transcript"
    >
      인식한 말: {{ heard }}
    </p>
    <p v-if="enabled && partial" class="hands-free-transcript">{{ partial }}</p>
    <p v-if="!supported" class="muted">
      음성 대기는 HTTPS 또는 localhost의 음성 인식 지원 브라우저에서 사용할 수
      있습니다.
    </p>
    <p v-if="aliases.length && !enabled" class="hands-free-notice">
      내 발음 {{ aliases.length }}개 등록됨
    </p>
    <details v-if="supported && !enabled" class="hands-free-help">
      <summary>호출어 사용 안내</summary>
      <p>
        켜면 “시리”라고 부른 뒤 질문하세요. 말이 끝나면 바로 전송합니다. 대기 중
        음성은 브라우저 제공 음성 인식 서비스에서 처리합니다. 화면을 벗어나면
        꺼집니다.
      </p>
    </details>
    <p v-if="enabled" class="hands-free-notice">
      마이크 대기 중 · 브라우저 음성 인식 서비스 사용
    </p>
    <p v-if="error" class="hands-free-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="hands-free-notice" role="status">{{ notice }}</p>
    <WakeEnrollment
      v-if="enrollment"
      :profile-key="profileKey"
      @close="closeEnrollment"
      @saved="aliases = $event"
      @listening="$emit('listening', $event)"
    />
  </div>
</template>
