<script setup>
import { ref, computed, watch, onUnmounted } from "vue";
import { Mic, Square, ArrowUp, RotateCcw, X, BookOpen } from "@lucide/vue";
import { isMock } from "../api/client";
import { transcribe } from "../api/stt";
import {
  browserListen,
  recordAudio,
  recognitionAvailable,
  voiceCommand,
  speakLocally,
} from "../api/voice";
const props = defineProps({
  disabled: Boolean,
  modelValue: { type: String, default: "" },
  asOf: { type: String, default: "" },
});
const emit = defineEmits([
  "confirmed",
  "update:modelValue",
  "update:asOf",
  "active",
]);
const engine = ref(isMock ? "browser" : "server"),
  phase = ref("idle"),
  transcript = ref(props.modelValue),
  partial = ref(""),
  error = ref(""),
  level = ref(0),
  elapsed = ref(0),
  handsFree = ref(true),
  readAnswer = ref(true),
  example = ref(false);
const browserSupported = recognitionAvailable();
const isCapturing = computed(() =>
  ["listening", "confirming"].includes(phase.value),
);
const active = computed(() => !["idle", "error"].includes(phase.value));
const phaseText = computed(
  () =>
    ({
      idle: "제조 중에도 말로 레시피를 물어보세요.",
      permission: "마이크 연결을 기다리고 있어요.",
      listening: "메뉴명과 궁금한 양·순서를 말해 주세요.",
      transcribing: "말씀하신 내용을 글로 바꾸고 있어요.",
      reading: "인식 내용을 읽어드리고 있어요.",
      review: "메뉴명·HOT/ICED·숫자가 맞는지 확인해 주세요.",
      confirming:
        "맞으면 “보내기”, 바꾸려면 “다시”, 끝내려면 “취소”라고 말해 주세요.",
      error: "음성 입력을 다시 시작할 수 있어요.",
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
  partial.value = "";
}
async function listen(purpose, signal, session) {
  phase.value = "permission";
  elapsed.value = 0;
  partial.value = "";
  stopCapture = null;
  timer = setInterval(() => elapsed.value++, 1000);
  const options = {
    signal,
    maxSeconds: purpose === "confirm" ? 10 : 60,
    onPartial: (text) => {
      if (session === run) {
        partial.value = text;
        if (purpose === "question")
          transcript.value = [prefix, text].filter(Boolean).join(" ");
      }
    },
    onLevel: (value) => {
      if (session === run) level.value = value;
    },
    onStopReady: (stop) => {
      if (session === run) {
        stopCapture = stop;
        phase.value = purpose === "confirm" ? "confirming" : "listening";
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
async function confirmByVoice(session = run) {
  if (!transcript.value || props.disabled) return;
  if (!controller || controller.signal.aborted)
    controller = new AbortController();
  const signal = controller.signal;
  try {
    if (handsFree.value) {
      phase.value = "reading";
      await speakLocally(
        `${props.asOf ? new Date(props.asOf).toLocaleString("ko-KR") + " 기준입니다." : "현재 기준입니다."} 인식한 질문은 ${transcript.value}. 맞으면 보내기, 바꾸려면 다시, 끝내려면 취소라고 말해 주세요.`,
        { signal },
      );
    }
    if (signal.aborted || session !== run) return;
    const command = voiceCommand(await listen("confirm", signal, session));
    if (signal.aborted || session !== run) return;
    phase.value = "review";
    if (command === "send") confirm();
    else if (command === "cancel") cancel();
    else if (command === "retry") start(true);
    else
      error.value =
        "확인 명령을 알아듣지 못했습니다. “말로 확인”을 누르거나 아래 확인 버튼을 이용하세요. 질문은 전송하지 않았습니다.";
  } catch (e) {
    if (session !== run || signal.aborted) return;
    phase.value = "review";
    error.value = e.message;
  }
}
async function start(retry = false) {
  if (props.disabled) return;
  if (!retry) prefix = transcript.value.trim();
  cancel();
  const session = run;
  controller = new AbortController();
  const signal = controller.signal;
  example.value = false;
  try {
    const text = (await listen("question", signal, session)).trim();
    if (signal.aborted || session !== run) return;
    if (!text || text.length > 2000)
      throw new Error("질문을 짧게 다시 말해 주세요. 최대 2,000자입니다.");
    transcript.value = [prefix, text].filter(Boolean).join(" ");
    if (transcript.value.length > 2000) {
      transcript.value = prefix;
      throw new Error("질문은 최대 2,000자입니다. 입력 내용을 줄여 주세요.");
    }
    phase.value = "review";
    if (handsFree.value) await confirmByVoice(session);
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
    read = readAnswer.value;
  cancel();
  emit("confirmed", { text, readAnswer: read });
}
function edit() {
  cancel();
  document.getElementById("question-input")?.focus();
}
function demo() {
  cancel();
  example.value = true;
  transcript.value = "아이스 바닐라라테 시럽 몇 펌프예요?";
  phase.value = "review";
}
watch(
  () => props.modelValue,
  (value) => {
    transcript.value = value;
  },
);
watch(transcript, (value) => emit("update:modelValue", value));
watch(active, (value) => emit("active", value));
function submit() {
  if (["idle", "error", "review"].includes(phase.value)) confirm();
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
    <label class="sr-only" for="question-input">질문 입력</label>
    <textarea
      id="question-input"
      v-model="transcript"
      maxlength="2000"
      placeholder="질문을 입력하거나 마이크를 눌러 말해 주세요."
      :disabled="disabled || (active && phase !== 'review')"
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
      <span v-if="isCapturing"
        >{{ elapsed }}초 / {{ phase === "confirming" ? 10 : 60 }}초</span
      >
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
      <template v-if="phase === 'review'">
        <button
          type="button"
          class="button secondary small"
          :disabled="disabled"
          @click="confirmByVoice()"
        >
          <Mic :size="17" />말로 확인
        </button>
        <button
          type="button"
          class="button secondary small"
          :disabled="disabled"
          @click="start(true)"
        >
          <RotateCcw :size="17" />다시 말하기
        </button>
        <button type="button" class="button ghost small" @click="edit">
          계속 입력하기
        </button>
      </template>
      <button type="button" class="button ghost small" @click="cancel">
        <X :size="17" />음성 입력 취소
      </button>
    </div>
    <p v-if="error" class="error-notice" role="alert">{{ error }}</p>
    <div class="composer-footer">
      <div class="composer-options">
        <span><BookOpen :size="14" />문서 기반 답변</span>
        <label class="date-option"
          >기준 시점<input
            :value="asOf"
            type="datetime-local"
            aria-label="답변 기준 시점"
            :disabled="disabled || active"
            @input="$emit('update:asOf', $event.target.value)"
        /></label>
      </div>
      <div class="composer-controls">
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
          @click="start(false)"
        >
          <Mic :size="22" />
        </button>
        <button
          class="send-button"
          aria-label="질문 보내기"
          :disabled="
            disabled ||
            (active && phase !== 'review') ||
            !transcript.trim() ||
            transcript.length > 2000
          "
        >
          <ArrowUp :size="21" />
        </button>
      </div>
    </div>
    <details class="voice-settings">
      <summary>음성 설정</summary>
      <div class="voice-options">
        <label
          >인식 방식<select v-model="engine" :disabled="active || disabled">
            <option value="server">STT 서버 · WebM/WAV</option>
            <option value="browser" :disabled="!browserSupported">
              브라우저 음성 체험{{ browserSupported ? "" : " (미지원)" }}
            </option>
          </select></label
        >
        <label class="checkbox-label"
          ><input
            v-model="handsFree"
            type="checkbox"
            :disabled="active || disabled"
          />말로 확인하기</label
        >
        <label class="checkbox-label"
          ><input
            v-model="readAnswer"
            type="checkbox"
            :disabled="active || disabled"
          />답변 읽어주기</label
        >
      </div>
      <p v-if="engine === 'browser'" class="voice-engine-note">
        브라우저 제공 음성 인식 서비스가 처리하며 네트워크를 사용할 수 있습니다.
        한국어 인식을 지원하는 브라우저에서 이용하세요.
      </p>
      <p v-else-if="isMock" class="voice-engine-note">
        샘플 모드에서는 STT 서버에 연결하지 않습니다. 실제 서버 모드로
        전환하거나 브라우저 음성 체험을 선택해 주세요.
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
    </details>
    <p class="voice-engine-note composer-voice-hint">
      입력하거나 말한 내용은 위 입력창에서 수정할 수 있습니다. 말이 끝나면
      메뉴명·HOT/ICED·수량을 확인한 뒤 “보내기”라고 말하세요. 최대 60초이며
      취소·화면 이탈 시 마이크가 꺼집니다.
    </p>
  </form>
</template>
