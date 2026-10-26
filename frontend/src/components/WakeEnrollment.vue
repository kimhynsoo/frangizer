<script setup>
import { computed, onUnmounted, ref } from "vue";
import { Mic, Square, X } from "@lucide/vue";
import { browserListen } from "../api/voice";
import {
  clearWakeProfile,
  loadWakeProfile,
  normalizeWakeAlias,
  saveWakeProfile,
  validWakeAlias,
} from "../api/wake-profile";
const props = defineProps({ profileKey: { type: String, required: true } });
const emit = defineEmits(["close", "saved", "listening"]);
const samples = ref([]),
  recording = ref(false),
  partial = ref(""),
  error = ref(""),
  notice = ref(""),
  existing = ref(loadWakeProfile(props.profileKey));
let controller,
  generation = 0;
const stopCapture = ref(null);
const ready = computed(
  () =>
    samples.value.length >= 3 &&
    samples.value.some((sample) => sample.selected),
);
function cancel() {
  generation++;
  controller?.abort();
  controller = null;
  recording.value = false;
  stopCapture.value = null;
  emit("listening", false);
}
function close() {
  cancel();
  emit("close");
}
async function capture() {
  if (recording.value || samples.value.length >= 5) return;
  const run = ++generation;
  controller = new AbortController();
  const { signal } = controller;
  recording.value = true;
  partial.value = "";
  error.value = "";
  notice.value = "";
  emit("listening", true);
  try {
    const text = await browserListen({
      signal,
      maxSeconds: 12,
      onPartial: (text) => {
        if (run === generation) partial.value = text;
      },
      onStopReady: (stop) => {
        if (run === generation) stopCapture.value = stop;
      },
    });
    if (signal.aborted || run !== generation) return;
    if (!validWakeAlias(text)) {
      error.value =
        "호출어만 짧게 말해 주세요. 질문이나 주변 대화는 등록하지 마세요";
      return;
    }
    samples.value.push({
      text: text.trim(),
      alias: normalizeWakeAlias(text),
      selected: false,
    });
    partial.value = "";
  } catch (e) {
    if (!signal.aborted && run === generation) error.value = e.message;
  } finally {
    if (run === generation) {
      recording.value = false;
      stopCapture.value = null;
      emit("listening", false);
    }
  }
}
function save() {
  if (!ready.value || recording.value) return;
  try {
    const aliases = saveWakeProfile(
      props.profileKey,
      samples.value
        .filter((sample) => sample.selected)
        .map((sample) => sample.alias),
    );
    emit("saved", aliases);
    close();
  } catch (e) {
    error.value = e.message;
  }
}
function reset() {
  if (recording.value) return;
  try {
    clearWakeProfile(props.profileKey);
    existing.value = [];
    samples.value = [];
    emit("saved", []);
    notice.value = "저장된 발음을 초기화했어요";
    error.value = "";
  } catch (e) {
    error.value = e.message;
  }
}
onUnmounted(cancel);
</script>
<template>
  <Teleport to="body">
    <div class="wake-enrollment-backdrop" @click.self="close">
      <section
        class="wake-enrollment"
        role="dialog"
        aria-modal="true"
        aria-label="내 발음 등록"
        v-focus-trap="close"
      >
        <header>
          <h2>내 발음 등록</h2>
          <button
            type="button"
            class="icon-button"
            aria-label="발음 등록 닫기"
            @click="close"
          >
            <X :size="20" />
          </button>
        </header>
        <p>
          평소 부르는 발음으로 <strong>“시리”만 3~5번</strong> 말해 주세요. 한
          번씩 듣고, 실제 인식된 표현을 확인해요.
        </p>
        <p class="wake-enrollment-note">
          목소리 자체를 학습하는 기능이 아닙니다. 선택한 표현만 이
          계정·브라우저에 저장하며 녹음 파일은 저장하지 않아요. 인식은 브라우저
          음성 서비스가 처리해요.
        </p>
        <p v-if="existing.length" class="wake-existing">
          현재 등록: {{ existing.join(", ") }}
        </p>
        <div class="wake-capture-row">
          <button
            v-if="!recording"
            type="button"
            class="button secondary"
            :disabled="samples.length >= 5"
            @click="capture"
          >
            <Mic :size="18" />{{
              samples.length >= 5
                ? "5회 수집 완료"
                : `${samples.length + 1}번째 발음 듣기`
            }}
          </button>
          <template v-else
            ><span role="status">“시리”라고 말해 주세요</span
            ><button
              type="button"
              class="button secondary small"
              :disabled="!stopCapture"
              @click="stopCapture?.()"
            >
              <Square :size="16" />말하기 끝</button
            ><button type="button" class="button ghost small" @click="cancel">
              취소
            </button></template
          >
        </div>
        <p v-if="recording && partial" class="wake-partial">
          인식 중: {{ partial }}
        </p>
        <p class="wake-progress" role="status">
          {{ samples.length }} / 최소 3회 · 호출어로 사용할 표현에 체크해 주세요
        </p>
        <ul class="wake-samples">
          <li v-for="(sample, index) in samples" :key="index">
            <label
              ><input
                v-model="sample.selected"
                type="checkbox"
                :disabled="recording"
              /><span>{{ index + 1 }}회: {{ sample.text }}</span></label
            >
          </li>
        </ul>
        <p v-if="error" class="wake-error" role="alert">{{ error }}</p>
        <p v-if="notice" role="status">{{ notice }}</p>
        <footer>
          <button
            type="button"
            class="button ghost small"
            :disabled="recording || !existing.length"
            @click="reset"
          >
            저장된 발음 초기화</button
          ><button
            type="button"
            class="button"
            :disabled="!ready || recording"
            @click="save"
          >
            선택한 발음 저장
          </button>
        </footer>
      </section>
    </div>
  </Teleport>
</template>
<style scoped>
.wake-enrollment-backdrop {
  position: fixed;
  inset: 0;
  z-index: 100;
  padding: 16px;
  display: flex;
  justify-content: center;
  align-items: center;
  background: #102c2466;
}
.wake-enrollment {
  width: min(480px, 100%);
  max-height: 90dvh;
  overflow-y: auto;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 18px;
  padding: 24px;
  color: var(--ink);
}
.wake-enrollment header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 18px;
}
.wake-enrollment h2 {
  margin: 0;
  font-size: 21px;
}
.wake-enrollment p {
  font-size: 13px;
  line-height: 1.8;
  margin: 12px 0;
  overflow-wrap: anywhere;
}
.wake-enrollment .wake-enrollment-note {
  font-size: 11px;
  color: var(--muted);
}
.wake-capture-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  padding: 18px 0;
  border-block: 1px solid var(--line);
  margin: 18px 0;
}
.wake-capture-row span,
.wake-progress {
  font-size: 12px;
}
.wake-samples {
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.wake-samples label {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  background: var(--sage);
  border-radius: 10px;
  margin: 0;
  font-size: 13px;
  overflow-wrap: anywhere;
}
.wake-samples input {
  width: 18px;
  height: 18px;
  flex-shrink: 0;
}
.wake-enrollment footer {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 12px;
  margin-top: 24px;
}
.wake-enrollment .wake-error {
  color: #883e30;
}
</style>
