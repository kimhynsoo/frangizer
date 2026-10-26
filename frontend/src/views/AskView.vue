<script setup>
import { storeToRefs } from "pinia";
import { useAuth } from "../stores/auth";
import { useChatTabs } from "../stores/chat-tabs";
import AskChat from "../components/AskChat.vue";
const auth = useAuth();
const chats = useChatTabs();
chats.activate(auth.user.id);
const { tabs, activeId, voiceActive } = storeToRefs(chats);
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
    <div
      v-for="tab in tabs"
      :key="tab.id"
      v-show="tab.id === activeId"
      role="tabpanel"
      :id="`chat-panel-${tab.id}`"
      :aria-label="tab.title"
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
