<script setup>
import { ref, nextTick, onMounted, onUnmounted } from "vue";
import { Sparkles, ThumbsUp, ThumbsDown } from "@lucide/vue";
import { useAuth } from "../stores/auth";
import * as api from "../api/rag";
import { newKey } from "../api/client";
import { speakLocally } from "../api/voice";
import SourcePanel from "../components/SourcePanel.vue";
import StatusBadge from "../components/StatusBadge.vue";
import ErrorNotice from "../components/ErrorNotice.vue";
import AnswerDetails from "../components/AnswerDetails.vue";
import VoiceInput from "../components/VoiceInput.vue";
const auth = useAuth(),
  userId = auth.user.id;
const question = ref(
    sessionStorage.getItem(`frangizer.question-draft.${userId}`) || "",
  ),
  asOf = ref(""),
  busy = ref(false),
  error = ref(null),
  messages = ref([]),
  source = ref(null),
  conversationId = ref(null),
  activeQuestion = ref(""),
  feedbackState = ref({}),
  feedbackError = ref(null),
  bottom = ref(null),
  voiceActive = ref(false),
  speechNotice = ref(""),
  readingAnswer = ref(false),
  readingQuestionId = ref(null);
let pending = null,
  answerSpeech = null,
  disposed = false;
async function send(retry = false, { readAnswer = false } = {}) {
  if (busy.value || voiceActive.value) return;
  stopReading();
  speechNotice.value = "";
  if (!retry) {
    const text = question.value.trim();
    if (!text || text.length > 2000) return;
    pending = {
      key: newKey(),
      localId: crypto.randomUUID(),
      body: {
        question: text,
        conversation_id: conversationId.value,
        ...(asOf.value ? { as_of: new Date(asOf.value).toISOString() } : {}),
      },
      readAnswer,
    };
    messages.value.push({
      id: pending.localId,
      question: text,
      result: null,
      status: "processing",
      errorCode: null,
    });
  }
  if (!pending) return;
  const attempt = pending;
  const message = messages.value.find((item) => item.id === attempt.localId);
  if (message) message.status = "processing";
  busy.value = true;
  error.value = null;
  activeQuestion.value = attempt.body.question;
  sessionStorage.setItem(
    `frangizer.question-draft.${userId}`,
    attempt.body.question,
  );
  try {
    const result = (await api.ask(attempt.body, attempt.key)).data;
    if (disposed) return;
    if (message) {
      message.result = result;
      message.status = result.status;
      message.errorCode = null;
    }
    conversationId.value = result.conversation_id;
    sessionStorage.setItem(
      "frangizer.conversation",
      `${userId}:${result.conversation_id}`,
    );
    question.value = "";
    sessionStorage.removeItem(`frangizer.question-draft.${userId}`);
    pending = null;
    await nextTick();
    bottom.value?.scrollIntoView({ behavior: "smooth", block: "end" });
    if (attempt.readAnswer && auth.user.role !== "prospect")
      startReading(result);
  } catch (e) {
    if (disposed) return;
    error.value = e;
    if (message) {
      message.status = "system_error";
      message.errorCode = e.response?.data?.error?.code || "NETWORK_ERROR";
    }
  } finally {
    if (!disposed) busy.value = false;
  }
}
function saveDraft() {
  sessionStorage.setItem(`frangizer.question-draft.${userId}`, question.value);
}
function stopReading() {
  answerSpeech?.abort();
  readingAnswer.value = false;
  readingQuestionId.value = null;
}
async function startReading(result) {
  if (auth.user.role === "prospect" || voiceActive.value || disposed) return;
  stopReading();
  speechNotice.value = "";
  const speech = new AbortController();
  answerSpeech = speech;
  readingQuestionId.value = result.question_id;
  readingAnswer.value = true;
  try {
    const ok = await speakLocally(
      `${result.answer} ${(result.steps || []).join(" ")}`,
      { signal: speech.signal },
    );
    if (!ok && !disposed && !speech.signal.aborted)
      speechNotice.value =
        "기기에 설치된 한국어 음성이 없어 답변을 읽지 못했습니다. 화면에서 답변을 확인해 주세요.";
  } catch {
    if (!disposed && !speech.signal.aborted)
      speechNotice.value = "답변을 읽지 못했습니다. 다시 시도해 주세요.";
  } finally {
    if (answerSpeech === speech) {
      readingAnswer.value = false;
      readingQuestionId.value = null;
    }
  }
}
function toggleReading(result) {
  if (readingAnswer.value && readingQuestionId.value === result.question_id)
    stopReading();
  else startReading(result);
}
function setReadPreference(enabled) {
  if (!enabled) {
    if (pending) pending.readAnswer = false;
    stopReading();
  }
}
function setVoiceActive(value) {
  voiceActive.value = value;
  if (value) stopReading();
}
async function submitQuestion(payload) {
  voiceActive.value = false;
  question.value = payload.text;
  saveDraft();
  await send(false, payload);
}
function reenter(message) {
  if (busy.value || voiceActive.value) return;
  question.value = message.question;
  saveDraft();
  document.getElementById("question-input")?.focus();
}
async function helpful(result, value) {
  feedbackError.value = null;
  try {
    await api.feedback(result.question_id, value);
    feedbackState.value[result.question_id] = value;
  } catch (e) {
    feedbackError.value = e;
  }
}
async function loadConversation() {
  const saved = sessionStorage.getItem("frangizer.conversation");
  if (!saved?.startsWith(`${userId}:`)) return;
  const cid = saved.slice(userId.length + 1);
  try {
    const response = await api.conversation(cid);
    if (disposed) return;
    conversationId.value = cid;
    messages.value = api.conversationMessages(response.data.messages);
    error.value = null;
  } catch (e) {
    error.value = e;
  }
}
onMounted(loadConversation);
onUnmounted(() => {
  disposed = true;
  answerSpeech?.abort();
});
</script>
<template>
  <section class="ask-page chat-page">
    <div class="page-heading">
      <div>
        <span class="eyebrow">KNOWLEDGE ASSISTANT</span>
        <h1>
          {{
            auth.user.role === "prospect"
              ? "가맹 준비, 무엇이 궁금하세요?"
              : "오늘의 운영, 무엇이 궁금하세요?"
          }}
        </h1>
      </div>
    </div>
    <p v-if="speechNotice" class="info-banner" role="status">
      {{ speechNotice }}
    </p>
    <div
      v-if="messages.length"
      class="conversation chat-thread"
      role="log"
      aria-label="질문과 답변"
    >
      <article
        v-for="message in messages"
        :key="message.id"
        class="message-pair"
      >
        <div class="question-bubble">
          <span class="sr-only">내 질문: </span>{{ message.question }}
        </div>
        <div class="answer-card">
          <div class="answer-heading">
            <span class="assistant-avatar"><Sparkles :size="17" /></span
            ><strong>프랜자이저</strong><StatusBadge :status="message.status" />
          </div>
          <template v-if="message.result">
            <AnswerDetails
              :result="message.result"
              :question="message.question"
              :disable-speech="voiceActive"
              :allow-read-aloud="auth.user.role !== 'prospect'"
              :external-reading="
                readingAnswer &&
                readingQuestionId === message.result.question_id
              "
              @toggle-read="toggleReading(message.result)"
              @source="source = $event"
            />
            <div class="answer-footer">
              <span>이 답변이 도움이 되었나요?</span
              ><button
                class="icon-button"
                :class="{
                  chosen: feedbackState[message.result.question_id] === true,
                }"
                aria-label="도움 됐어요"
                @click="helpful(message.result, true)"
              >
                <ThumbsUp :size="16" /></button
              ><button
                class="icon-button"
                :class="{
                  chosen: feedbackState[message.result.question_id] === false,
                }"
                aria-label="도움이 안 됐어요"
                @click="helpful(message.result, false)"
              >
                <ThumbsDown :size="16" /></button
              ><small v-if="message.result.question_id in feedbackState"
                >의견이 저장됐습니다.</small
              >
            </div>
          </template>
          <div v-else class="history-state">
            <p v-if="message.status === 'processing'">
              이 질문은 처리 중입니다. 잠시 후 대화 상태를 새로 확인해 주세요.
            </p>
            <template v-else
              ><p>
                질문을 처리하지 못했습니다. 근거 부족이 아닌 시스템 또는 연결
                오류입니다.
              </p>
              <small v-if="message.errorCode"
                >오류 코드 · {{ message.errorCode }}</small
              ></template
            >
            <div class="voice-actions">
              <button
                class="button small secondary"
                :disabled="busy || voiceActive"
                @click="loadConversation"
              >
                대화 상태 새로고침</button
              ><button
                class="button small ghost"
                :disabled="busy || voiceActive"
                @click="reenter(message)"
              >
                질문 다시 입력
              </button>
            </div>
          </div>
        </div>
      </article>
    </div>
    <p v-if="busy" class="loading-line" role="status">
      <span class="pulse-dot"></span>‘{{ activeQuestion }}’에 필요한 문서를
      확인하고 있어요…
    </p>
    <ErrorNotice
      :error="error"
      @retry="pending ? send(true) : loadConversation()"
    /><ErrorNotice :error="feedbackError" />
    <div ref="bottom"></div>
    <div class="chat-composer">
      <VoiceInput
        v-model="question"
        v-model:as-of="asOf"
        :disabled="busy"
        :allow-read-aloud="auth.user.role !== 'prospect'"
        :reading-answer="readingAnswer"
        @stop-reading="stopReading"
        @active="setVoiceActive"
        @confirmed="submitQuestion"
        @read-preference="setReadPreference"
        @update:model-value="saveDraft"
      />
      <p class="composer-note">
        개인정보 입력을 피해주세요. 답변의 출처와 적용일을 확인하고, 필요한
        결정은 담당자와 확정하세요.
      </p>
    </div>
  </section>
  <SourcePanel v-if="source" :source="source" @close="source = null" />
</template>
