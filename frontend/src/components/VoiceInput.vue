<script setup>
import { ref, computed, watch, onUnmounted } from "vue";
import {
  Mic,
  Square,
  ArrowUp,
  X,
  Volume2,
  VolumeX,
} from "@lucide/vue";
import { isMock } from "../api/client";
import { transcribe } from "../api/stt";
import { browserListen, recordAudio, recognitionAvailable } from "../api/voice";
const props = defineProps({
  inputId: { type: String, default: "question-input" },
  disabled: Boolean,
  readingAnswer: Boolean,
  allowReadAloud: { type: Boolean, default: true },
  modelValue: { type: String, default: "" },
});
const emit = defineEmits([
  "confirmed",
  "update:modelValue",
  "active",
  "readPreference",
  "stopReading",
]);
const browserSupported = recognitionAvailable();
const engine = ref(browserSupported ? "browser" : "server"),
  phase = ref("idle"),
  transcript = ref(props.modelValue),
  error = ref(""),
  level = ref(0),
  elapsed = ref(0),
  readAnswer = ref(false),
  example = ref(false);
const isCapturing = computed(() => phase.value === "listening");
const active = computed(() => !["idle", "error"].includes(phase.value));
const phaseText = computed(
  () =>
    ({
      idle: "제조 중에도 말로 레시피를 물어보세요",
      permission: "마이크 연결을 기다리고 있어요",
      listening: "메뉴명과 궁금한 양·순서를 말해 주세요",
      transcribing: "말씀하신 내용을 글로 바꾸고 있어요",
      error: "음성 입력을 다시 시작할 수 있어요",
    })[phase.value],
);
let controller = null,
  run = 0,
  timer = null,
  stopCapture = null,
  prefix = "";
function clearClock() {
  clearInterval(timer);
  timer = null;
  level.value = 0;
  stopCapture = null;
}
function cancel() {
  if (["permission", "listening", "transcribing"].includes(phase.value))
    transcript.value = prefix;
  run++;
  controller?.abort();
  controller = null;
  clearClock();
  phase.value = "idle";
  error.value = "";
}
async function listen(signal, session) {
  phase.value = "permission";
  elapsed.value = 0;
  stopCapture = null;
  timer = setInterval(() => elapsed.value++, 1000);
  const options = {
    signal,
    maxSeconds: 60,
    onPartial: (text) => {
      if (session === run && !signal.aborted) {
        transcript.value = [prefix, text].filter(Boolean).join(" ");
      }
    },
    onLevel: (value) => {
      if (session === run) level.value = value;
    },
    onStopReady: (stop) => {
      if (session === run) {
        stopCapture = stop;
        phase.value = "listening";
      }
    },
  };
  try {
    if (engine.value === "browser") return await browserListen(options);
    let blob = await recordAudio(options);
    if (signal.aborted) throw new DOMException("취소", "AbortError");
    phase.value = "transcribing";
    clearClock();
    try {
      return (await transcribe(blob, { signal })).text;
    } finally {
      blob = null;
    }
  } finally {
    if (session === run) clearClock();
  }
}
async function start() {
  if (props.disabled) return;
  prefix = transcript.value.trim();
  cancel();
  const session = run;
  controller = new AbortController();
  const signal = controller.signal;
  example.value = false;
  try {
    const text = (await listen(signal, session)).trim();
    if (signal.aborted || session !== run) return;
    if (!text || text.length > 2000)
      throw new Error(
        "질문은 최대 2,000자까지 입력할 수 있습니다. 짧게 다시 말해 주세요",
      );
    transcript.value = [prefix, text].filter(Boolean).join(" ");
    if (transcript.value.length > 2000) {
      transcript.value = prefix;
      throw new Error("질문은 최대 2,000자입니다. 입력 내용을 줄여 주세요");
    }
    phase.value = "idle";
    document.getElementById(props.inputId)?.focus();
  } catch (e) {
    if (session !== run || signal.aborted) return;
    transcript.value = prefix;
    phase.value = "error";
    error.value = e.message;
  }
}
function confirm() {
  if (props.disabled || !transcript.value.trim()) return;
  const text = transcript.value.trim(),
    read = props.allowReadAloud && readAnswer.value;
  cancel();
  emit("confirmed", { text, readAnswer: read });
}
function demo() {
  cancel();
  example.value = true;
  transcript.value = "아이스 바닐라라테 시럽 몇 펌프예요?";
  phase.value = "idle";
}
watch(
  () => props.modelValue,
  (value) => {
    transcript.value = value;
  },
);
watch(transcript, (value) => emit("update:modelValue", value));
watch(active, (value) => emit("active", value));
watch(readAnswer, (value) => emit("readPreference", value));
function submit() {
  if (["idle", "error"].includes(phase.value)) confirm();
}
function onEnter(event) {
  if (!event.isComposing) {
    event.preventDefault();
    submit();
  }
}
watch(
  () => props.disabled,
  (value) => {
    if (value && active.value) cancel();
  },
);
function onVisibility() {
  if (document.hidden) cancel();
}
function onKey(event) {
  if (event.key === "Escape" && active.value) cancel();
}
document.addEventListener("visibilitychange", onVisibility);
document.addEventListener("keydown", onKey);
onUnmounted(() => {
  cancel();
  document.removeEventListener("visibilitychange", onVisibility);
  document.removeEventListener("keydown", onKey);
  emit("active", false);
});
</script>
<template>
  <form
    class="composer unified-composer"
    aria-label="질문 작성"
    @submit.prevent="submit"
  >
    <label class="sr-only" :for="inputId">질문 입력</label>
    <textarea
      :id="inputId"
      v-model="transcript"
      maxlength="2000"
      placeholder="질문을 입력하거나 마이크를 눌러 말해 주세요"
      :disabled="disabled || active"
      @input="example = false"
      @keydown.enter.exact="onEnter"
    ></textarea>
    <div
      v-if="active || error"
      class="voice-status"
      role="status"
      aria-live="polite"
    >
      <strong>{{ phaseText }}</strong>
      <span v-if="isCapturing">{{ elapsed }}초 / 60초</span>
    </div>
    <div v-if="isCapturing" class="voice-level" aria-hidden="true">
      <span :style="{ width: `${Math.max(4, level * 100)}%` }"></span>
    </div>
    <span v-if="example" class="badge synthetic"
      >예시 문장 · 실제 음성 인식 아님</span
    >
    <div v-if="active" class="voice-actions">
      <button
        v-if="isCapturing"
        type="button"
        class="button secondary small"
        @click="stopCapture?.()"
      >
        <Square :size="17" />말하기 끝
      </button>
      <button type="button" class="button ghost small" @click="cancel">
        <X :size="17" />음성 입력 취소
      </button>
    </div>
    <p v-if="error" class="error-notice" role="alert">{{ error }}</p>
    <div class="composer-footer">
      <slot name="hands-free" />
      <div class="composer-controls">
        <button
          v-if="allowReadAloud && readingAnswer"
          type="button"
          class="button small reading-stop"
          @click="$emit('stopReading')"
        >
          <Square :size="16" />읽기 중지
        </button>
        <button
          v-if="allowReadAloud"
          type="button"
          class="button secondary small answer-read-toggle"
          :aria-pressed="readAnswer"
          :disabled="active"
          @click="readAnswer = !readAnswer"
        >
          <component :is="readAnswer ? Volume2 : VolumeX" :size="16" />
          {{ readAnswer ? "답변 자동 읽기 켜짐" : "답변 자동 읽기 꺼짐" }}
        </button>
        <small>{{ transcript.length }} / 2,000</small>
        <button
          type="button"
          class="mic-button"
          aria-label="말로 질문하기"
          :disabled="
            disabled ||
            active ||
            (engine === 'browser' && !browserSupported) ||
            (isMock && engine === 'server')
          "
          @click="start()"
        >
          <Mic :size="22" />
        </button>
        <button
          class="send-button"
          aria-label="질문 보내기"
          :disabled="
            disabled || active || !transcript.trim() || transcript.length > 2000
          "
        >
          <ArrowUp :size="21" />
        </button>
      </div>
    </div>
    <details class="voice-settings">
      <summary>음성 설정</summary>
      <slot name="voice-settings" />
      <div class="voice-options">
        <label
          >인식 방식<select v-model="engine" :disabled="active || disabled">
            <option value="server">STT 서버 · 녹음 후 입력</option>
            <option value="browser" :disabled="!browserSupported">
              실시간 음성 입력{{ browserSupported ? "" : " (미지원)" }}
            </option>
          </select></label
        >
      </div>
      <p v-if="engine === 'browser'" class="voice-engine-note">
        말하는 동안 인식한 내용이 질문창에 바로 입력됩니다. 브라우저 제공 음성
        인식 서비스가 처리하며 네트워크를 사용할 수 있습니다.
      </p>
      <p v-else-if="isMock" class="voice-engine-note">
        샘플 모드에서는 STT 서버에 연결하지 않습니다. 실제 서버 모드로
        전환하거나 실시간 음성 입력을 선택해 주세요
      </p>
      <p v-else class="voice-engine-note">
        녹음이 끝나면 인식 결과가 질문창에 입력됩니다. 실시간 입력은 한국어 음성
        인식을 지원하는 브라우저에서 이용하세요
      </p>
      <button
        v-if="isMock"
        type="button"
        class="button ghost small"
        :disabled="disabled || active"
        @click="demo"
      >
        녹음 없이 사용 흐름 보기
      </button>
      <p class="voice-engine-note composer-voice-hint">
        말한 내용은 질문창에 입력됩니다. 필요하면 수정한 뒤 엔터나 보내기
        버튼으로 질문할 수 있습니다. 최대 60초이며 취소·화면 이탈 시 마이크가
        꺼집니다.
      </p>
    </details>
  </form>
</template>
