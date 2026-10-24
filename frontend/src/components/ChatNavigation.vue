<script setup>
import { watch } from "vue";
import { storeToRefs } from "pinia";
import { useRoute, useRouter } from "vue-router";
import { Plus, X } from "@lucide/vue";
import { useAuth } from "../stores/auth";
import { useChatTabs } from "../stores/chat-tabs";
const emit = defineEmits(["navigate"]);
const auth = useAuth(),
  chats = useChatTabs(),
  route = useRoute(),
  router = useRouter();
const { tabs, activeId, voiceActive } = storeToRefs(chats);
watch(
  () => auth.user?.id,
  (id) => {
    if (id) chats.activate(id);
  },
  { immediate: true },
);
function open(id) {
  if (voiceActive.value) return;
  chats.select(id);
  router.push("/ask");
  emit("navigate");
}
function create() {
  if (voiceActive.value) return;
  chats.newChat();
  router.push("/ask");
  emit("navigate");
}
</script>
<template>
  <div class="sidebar-conversations" aria-label="질문하기 대화 목록">
    <button
      type="button"
      class="new-chat-button"
      :disabled="voiceActive"
      @click="create"
    >
      <Plus :size="14" />새 대화
    </button>
    <div
      class="sidebar-chat-list"
      role="tablist"
      aria-label="대화 목록"
      aria-orientation="vertical"
    >
      <div
        v-for="tab in tabs"
        :key="tab.id"
        class="sidebar-chat-item"
        :class="{ selected: route.name === 'ask' && tab.id === activeId }"
        role="presentation"
      >
        <button
          type="button"
          role="tab"
          :id="`chat-tab-${tab.id}`"
          :aria-selected="route.name === 'ask' && tab.id === activeId"
          :aria-controls="
            route.name === 'ask' ? `chat-panel-${tab.id}` : undefined
          "
          :title="tab.title"
          :disabled="voiceActive"
          @click="open(tab.id)"
        >
          {{ tab.title }}
        </button>
        <button
          type="button"
          class="chat-tab-close"
          :aria-label="`${tab.title} 대화 탭 삭제`"
          title="대화 삭제"
          :disabled="voiceActive"
          @click="chats.closeChat(tab.id)"
        >
          <X :size="13" />
        </button>
      </div>
    </div>
  </div>
</template>
