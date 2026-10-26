<script setup>
import { ref, watch, nextTick, onMounted, onUnmounted } from "vue";
import { Sparkles, ThumbsUp, ThumbsDown } from "@lucide/vue";
import { useAuth } from "../stores/auth";
import * as api from "../api/rag";
import { newKey, isMock } from "../api/client";
import { speakLocally } from "../api/voice";
import { spokenAnswer } from "../api/spoken-answer";
import { followUpPresentation, temperatureChoice } from "../api/follow-up";
import SourcePanel from "./SourcePanel.vue";
import StatusBadge from "./StatusBadge.vue";
import ErrorNotice from "./ErrorNotice.vue";
import AnswerDetails from "./AnswerDetails.vue";
import VoiceInput from "./VoiceInput.vue";
import HandsFreeVoice from "./HandsFreeVoice.vue";
const props = defineProps({
  tabId: String,
  initialConversationId: String,
  active: Boolean,
});
const emit = defineEmits(["conversation", "title", "voice-active"]);
const auth = useAuth(),
  userId = auth.user.id;
const exampleQuestions =
  auth.user.role === "prospect"
    ? [
        "가맹계약 전에 어떤 자료를 확인해야 하나요?",
        "가맹점 창업 비용에는 어떤 항목이 포함되나요?",
      ]
    : [
        "아이스 바닐라라테 시럽은 몇 펌프인가요?",
        "카페라테와 바닐라라테의 제조 순서는 어떻게 다른가요?",
      ];
function selectExample(text) {
  if (busy.value || loading.value || voiceActive.value) return;
  question.value = text;
  send();
}
const draftKey = `frangizer.question-draft.${userId}.${props.tabId}`;
const question = ref(sessionStorage.getItem(draftKey) || ""),
  busy = ref(false),
  loading = ref(false),
  error = ref(null),
  messages = ref([]),
  source = ref(null),
  conversationId = ref(props.initialConversationId || null),
  activeQuestion = ref(""),
  feedbackState = ref({}),
  feedbackError = ref(null),
  bottom = ref(null),
  voiceActive = ref(false),
  handsFreeEnabled = ref(false),
  handsFreeControl = ref(null),
  speechNotice = ref(""),
  readingAnswer = ref(false),
  readingQuestionId = ref(null);
let pending = null,
  answerSpeech = null,
  disposed = false;
async function send(retry = false, { readAnswer = false } = {}) {
  if (busy.value || loading.value || voiceActive.value) return;
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
      },
      readAnswer,
    };
    if (!messages.value.length) emit("title", text.slice(0, 24));
    messages.value.push({
      id: pending.localId,
      question: text,
      result: null,
      status: "processing",
      errorCode: null,
    });
    question.value = "";
    sessionStorage.removeItem(draftKey);
  }
  if (!pending) return;
  const attempt = pending;
  const message = messages.value.find((item) => item.id === attempt.localId);
  if (message) message.status = "processing";
  busy.value = true;
  error.value = null;
  activeQuestion.value = attempt.body.question;
  try {
    const result = (await api.ask(attempt.body, attempt.key)).data;
    if (disposed) return;
    if (message) {
      message.result = result;
      message.status = result.status;
      message.errorCode = null;
    }
    conversationId.value = result.conversation_id;
    emit("conversation", result.conversation_id);
    pending = null;
    await nextTick();
    if (props.active)
      bottom.value?.scrollIntoView({ behavior: "smooth", block: "end" });
    if (props.active && attempt.readAnswer && auth.user.role !== "prospect")
      startReading(result);
    return result;
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
async function sendFollowUp(text) {
  if (
    !conversationId.value ||
    !props.active ||
    busy.value ||
    loading.value ||
    (voiceActive.value && !handsFreeEnabled.value)
  )
    return;
  const resumeHandsFree = handsFreeEnabled.value;
  if (resumeHandsFree) handsFreeControl.value?.stop();
  question.value = text;
  const result = await send();
  if (resumeHandsFree && props.active && !disposed)
    handsFreeControl.value?.start();
  return result;
}
function saveDraft() {
  sessionStorage.setItem(draftKey, question.value);
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
      spokenAnswer(
        result,
        messages.value.find(
          (message) => message.result?.question_id === result.question_id,
        )?.question || "",
      ),
      { signal: speech.signal },
    );
    if (!ok && !disposed && !speech.signal.aborted)
      speechNotice.value =
        "기기에 설치된 한국어 음성이 없어 답변을 읽지 못했습니다. 화면에서 답변을 확인해 주세요";
  } catch {
    if (!disposed && !speech.signal.aborted)
      speechNotice.value = "답변을 읽지 못했습니다. 다시 시도해 주세요";
  } finally {
    if (answerSpeech === speech) {
      readingAnswer.value = false;
      readingQuestionId.value = null;
    }
  }
}
function toggleReading(result) {
  handsFreeControl.value?.stop();
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
  emit("voice-active", value);
  if (value) stopReading();
}
async function submitQuestion(payload) {
  voiceActive.value = false;
  const latestResult = messages.value.at(-1)?.result;
  const contextualChoice =
    latestResult && followUpPresentation(latestResult).temperature
      ? temperatureChoice(payload.text)
      : null;
  question.value = contextualChoice || payload.text;
  saveDraft();
  return await send(false, payload);
}
function reenter(message) {
  if (busy.value || loading.value || voiceActive.value) return;
  question.value = message.question;
  saveDraft();
  document.getElementById(`question-input-${props.tabId}`)?.focus();
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
  const cid = conversationId.value;
  if (!cid || loading.value || busy.value) return;
  loading.value = true;
  try {
    const response = await api.conversation(cid);
    if (disposed) return;
    conversationId.value = cid;
    messages.value = api.conversationMessages(response.data.messages);
    error.value = null;
  } catch (e) {
    if (!disposed) error.value = e;
  } finally {
    loading.value = false;
  }
}
watch(
  () => props.active,
  (active) => {
    if (!active) {
      stopReading();
      source.value = null;
    }
  },
);
onMounted(loadConversation);
onUnmounted(() => {
  disposed = true;
  answerSpeech?.abort();
});
</script>
<template>
  <section class="ask-page chat-page">
    <div class="chat-scroll-area">
      <p v-if="loading" role="status">대화를 불러오고 있어요…</p>
      <div
        v-if="!messages.length && !loading && !conversationId"
        class="chat-examples"
      >
        <p class="section-label">예시 질문</p>
        <div class="suggestion-grid">
          <button
            v-for="text in exampleQuestions"
            :key="text"
            type="button"
            class="suggestion-card"
            :disabled="voiceActive"
            @click="selectExample(text)"
          >
            <strong>{{ text }}</strong>
          </button>
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
              ><strong>프랜자이저</strong
              ><StatusBadge :status="message.status" />
            </div>
            <template v-if="message.result">
              <AnswerDetails
                :result="message.result"
                :question="message.question"
                :allow-follow-up="
                  active &&
                  message.id === messages.at(-1)?.id &&
                  !!conversationId
                "
                :follow-up-disabled="
                  busy || loading || (voiceActive && !handsFreeEnabled)
                "
                @follow-up="sendFollowUp"
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
                이 질문은 처리 중입니다. 잠시 후 대화 상태를 새로 확인해 주세요
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
    </div>
    <div class="chat-composer">
      <VoiceInput
        :input-id="`question-input-${tabId}`"
        v-model="question"
        :disabled="busy || loading || handsFreeEnabled"
        :allow-read-aloud="auth.user.role !== 'prospect'"
        :reading-answer="readingAnswer"
        @stop-reading="stopReading"
        @active="setVoiceActive"
        @confirmed="submitQuestion"
        @read-preference="setReadPreference"
        @update:model-value="saveDraft"
      >
        <template #hands-free>
          <HandsFreeVoice
            ref="handsFreeControl"
            compact
            :profile-key="`${isMock ? 'demo' : 'live'}:${userId}`"
            :ask="(text) => submitQuestion({ text, readAnswer: false })"
            :active="active"
            :disabled="busy || loading || voiceActive || readingAnswer"
            :allow-read-aloud="auth.user.role !== 'prospect'"
            @enabled="handsFreeEnabled = $event"
            @listening="setVoiceActive"
          />
        </template>
        <template #voice-settings>
          <div class="composer-wake-settings">
            <template v-if="handsFreeControl?.supported">
              <button
                type="button"
                class="button ghost small"
                :disabled="!handsFreeControl?.canRegister"
                @click="handsFreeControl?.openEnrollment()"
              >
                {{
                  handsFreeControl?.registrationCount
                    ? "내 발음 재등록"
                    : "내 발음 등록"
                }}
              </button>
              <span v-if="handsFreeControl?.registrationCount" class="muted"
                >내 발음 {{ handsFreeControl.registrationCount }}개 등록됨</span
              >
              <p class="voice-engine-note">
                음성 대기를 켜고 “시리”라고 부른 뒤 질문하세요. 말이 끝나면 자동
                전송합니다. 대기 중 음성은 브라우저 제공 인식 서비스에서
                처리하며 화면을 벗어나면 꺼집니다.
              </p>
            </template>
            <p v-else class="voice-engine-note">
              음성 대기는 HTTPS 또는 localhost의 음성 인식 지원 브라우저에서
              사용할 수 있습니다.
            </p>
          </div>
        </template>
      </VoiceInput>
      <p class="composer-note">
        개인정보 입력을 피하고, 답변의 출처와 적용일을 확인한 뒤 필요한 결정은
        담당자와 확정하세요
      </p>
    </div>
  </section>
  <SourcePanel v-if="source" :source="source" @close="source = null" />
</template>
