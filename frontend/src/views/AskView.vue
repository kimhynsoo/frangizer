<script setup>
import { ref, watch } from "vue";
import { Plus, X } from "@lucide/vue";
import { useAuth } from "../stores/auth";
import AskChat from "../components/AskChat.vue";
const auth = useAuth();
const userId = auth.user.id;
const storageKey = `frangizer.chat-tabs.${userId}`;
function restore() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey));
    if (
      Array.isArray(saved?.tabs) &&
      saved.tabs.length &&
      saved.tabs.every(
        (tab) =>
          typeof tab.id === "string" &&
          typeof tab.title === "string" &&
          (tab.conversationId === null ||
            typeof tab.conversationId === "string"),
      )
    )
      return saved;
  } catch {
    /* Ignore invalid stored tab metadata. */
  }
  const legacy = sessionStorage.getItem("frangizer.conversation");
  const tab = {
    id: crypto.randomUUID(),
    title: "대화 1",
    conversationId: legacy?.startsWith(`${userId}:`)
      ? legacy.slice(userId.length + 1)
      : null,
  };
  const draft = sessionStorage.getItem(`frangizer.question-draft.${userId}`);
  if (draft)
    sessionStorage.setItem(
      `frangizer.question-draft.${userId}.${tab.id}`,
      draft,
    );
  return { tabs: [tab], activeId: tab.id };
}
const saved = restore();
const tabs = ref(saved.tabs);
const activeId = ref(
  tabs.value.some((t) => t.id === saved.activeId)
    ? saved.activeId
    : tabs.value[0].id,
);
const voiceActive = ref(false);
function persist() {
  sessionStorage.setItem(
    storageKey,
    JSON.stringify({ tabs: tabs.value, activeId: activeId.value }),
  );
}
watch([tabs, activeId], persist, { deep: true });
persist();
function newChat() {
  if (voiceActive.value) return;
  const tab = {
    id: crypto.randomUUID(),
    title: "새 대화",
    conversationId: null,
  };
  tabs.value.push(tab);
  activeId.value = tab.id;
}
function closeChat(id) {
  if (voiceActive.value) return;
  const index = tabs.value.findIndex((tab) => tab.id === id);
  if (index < 0) return;
  sessionStorage.removeItem(`frangizer.question-draft.${userId}.${id}`);
  tabs.value.splice(index, 1);
  if (!tabs.value.length) newChat();
  else if (activeId.value === id)
    activeId.value = tabs.value[Math.min(index, tabs.value.length - 1)].id;
  persist();
}
</script>
<template>
  <div class="ask-tabs-page">
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
    <div class="chat-tabs-bar">
      <div class="chat-tabs" role="tablist" aria-label="대화 탭">
        <div
          v-for="tab in tabs"
          :key="tab.id"
          class="chat-tab"
          :class="{ selected: tab.id === activeId }"
          role="presentation"
        >
          <button
            type="button"
            role="tab"
            :id="`chat-tab-${tab.id}`"
            :aria-selected="tab.id === activeId"
            :aria-controls="`chat-panel-${tab.id}`"
            :disabled="voiceActive"
            @click="activeId = tab.id"
          >
            {{ tab.title }}
          </button>
          <button
            type="button"
            class="chat-tab-close"
            :aria-label="`${tab.title} 대화 탭 삭제`"
            title="대화 탭 삭제"
            :disabled="voiceActive"
            @click="closeChat(tab.id)"
          >
            <X :size="16" />
          </button>
        </div>
      </div>
      <button
        type="button"
        class="button small secondary new-chat-button"
        :disabled="voiceActive"
        @click="newChat"
      >
        <Plus :size="16" />새 대화
      </button>
    </div>
    <div
      v-for="tab in tabs"
      :key="tab.id"
      v-show="tab.id === activeId"
      role="tabpanel"
      :id="`chat-panel-${tab.id}`"
      :aria-labelledby="`chat-tab-${tab.id}`"
    >
      <AskChat
        :tab-id="tab.id"
        :initial-conversation-id="tab.conversationId"
        :active="tab.id === activeId"
        @conversation="tab.conversationId = $event"
        @title="tab.title = $event"
        @voice-active="voiceActive = $event"
      />
    </div>
  </div>
</template>
