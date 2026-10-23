<script setup>
import { ref, nextTick, onMounted, onUnmounted } from "vue";
import {
  BookOpen,
  Sparkles,
  Coffee,
  Store,
  ShieldCheck,
  MessageCircle,
  ThumbsUp,
  ThumbsDown,
  Plus,
} from "@lucide/vue";
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
  readingAnswer = ref(false);
let pending = null,
  answerSpeech = null,
  disposed = false;
const suggestions =
  auth.user.role === "prospect"
    ? [
        {
          icon: Store,
          tag: "가맹 준비",
          title: "가맹을 준비하려면 무엇부터 확인하나요?",
          text: "가맹카페 A의 가맹 준비 항목을 알려주세요.",
        },
        {
          icon: BookOpen,
          tag: "근거 확인",
          title: "추가 장비 구매 근거가 궁금해요",
          text: "추가 장비 구매 요구의 근거를 확인하고 싶어요.",
        },
      ]
    : [
        {
          icon: Coffee,
          tag: "레시피",
          title: "바닐라라테, 정확한 계량이 궁금해요",
          text: "아이스 바닐라라테의 재료와 제조 순서를 알려주세요.",
        },
        {
          icon: MessageCircle,
          tag: "고객 응대",
          title: "고객이 이물 발견으로 환불을 요청했어요",
          text: "음료에 이물이 있다는 고객의 환불 요청은 어떤 기준을 확인해야 하나요?",
        },
        {
          icon: Store,
          tag: "가맹 준비",
          title: "가맹을 준비하려면 무엇부터 확인하나요?",
          text: "가맹카페 A의 가맹 준비 항목을 알려주세요.",
        },
      ];
async function send(retry = false, { readAnswer = false } = {}) {
  if (busy.value || voiceActive.value) return;
  answerSpeech?.abort();
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
    if (attempt.readAnswer) {
      answerSpeech = new AbortController();
      const prefix =
        result.status === "answered"
          ? "등록된 문서 기준입니다."
          : result.status === "handoff"
            ? "담당자 확인이 필요합니다."
            : "추가 확인이 필요합니다.";
      readingAnswer.value = true;
      const speech = answerSpeech;
      speakLocally(
        `${prefix} ${result.basis !== "current" ? "과거 또는 예정 기준 답변입니다. " : ""}${result.answer} ${result.steps.join(" ")} ${result.handoff_reason || ""}`,
        { signal: speech.signal },
      )
        .then((ok) => {
          if (!ok && !disposed && !speech.signal.aborted)
            speechNotice.value =
              "기기에 설치된 한국어 음성이 없어 답변을 읽지 못했습니다. 화면에서 답변을 확인해 주세요.";
        })
        .finally(() => {
          if (speech === answerSpeech) readingAnswer.value = false;
        });
    }
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
function reset() {
  if (busy.value || voiceActive.value) return;
  answerSpeech?.abort();
  messages.value = [];
  conversationId.value = null;
  question.value = "";
  pending = null;
  error.value = null;
  sessionStorage.removeItem("frangizer.conversation");
  sessionStorage.removeItem(`frangizer.question-draft.${userId}`);
}
function stopReading() {
  answerSpeech?.abort();
  readingAnswer.value = false;
}
function setVoiceActive(value) {
  voiceActive.value = value;
  if (value) answerSpeech?.abort();
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
  <section class="ask-page">
    <div class="page-heading">
      <div>
        <span class="eyebrow">KNOWLEDGE ASSISTANT</span>
        <h1>오늘의 운영, 무엇이 궁금하세요?</h1>
        <p>가맹 준비부터 매장 운영까지, 문서 근거와 함께 확인하세요.</p>
      </div>
      <button
        class="button secondary small"
        :disabled="busy || voiceActive"
        @click="reset"
      >
        <Plus :size="16" />새 질문
      </button>
    </div>
    <button v-if="readingAnswer" class="button secondary" @click="stopReading">
      답변 읽기 중지
    </button>
    <p v-if="speechNotice" class="info-banner" role="status">
      {{ speechNotice }}
    </p>
    <div v-if="!messages.length" class="ask-welcome">
      <div class="welcome-symbol"><Sparkles :size="27" /></div>
      <h2>필요한 기준을 찾는<br /><span>가장 가까운 파트너.</span></h2>
      <p>질문을 남기면 승인된 문서에서 근거를 찾아 안내해 드려요.</p>
      <div class="suggestion-grid">
        <button
          v-for="suggestion in suggestions"
          :key="suggestion.tag"
          class="suggestion-card"
          @click="
            question = suggestion.text;
            saveDraft();
          "
          :disabled="busy || voiceActive"
        >
          <div>
            <span class="suggestion-icon"
              ><component :is="suggestion.icon" :size="20" /></span
            ><span class="suggestion-tag">{{ suggestion.tag }}</span
            ><ArrowUpRight :size="17" />
          </div>
          <strong>{{ suggestion.title }}</strong
          ><small>질문 입력하기</small>
        </button>
      </div>
      <div class="trust-strip">
        <ShieldCheck :size="17" /><span>문서 근거 확인</span><i></i
        ><span>적용일·판본 표시</span><i></i><span>확인 필요 사항 안내</span>
      </div>
    </div>
    <div v-else class="conversation">
      <article
        v-for="message in messages"
        :key="message.id"
        class="message-pair"
      >
        <div class="question-bubble">{{ message.question }}</div>
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
    <VoiceInput
      v-model="question"
      v-model:as-of="asOf"
      :disabled="busy"
      @active="setVoiceActive"
      @confirmed="submitQuestion"
      @update:model-value="saveDraft"
    />
    <p class="composer-note">
      개인정보 입력을 피해주세요. 답변의 출처와 적용일을 확인하고, 필요한 결정은
      담당자와 확정하세요.
    </p>
  </section>
  <SourcePanel v-if="source" :source="source" @close="source = null" />
</template>
