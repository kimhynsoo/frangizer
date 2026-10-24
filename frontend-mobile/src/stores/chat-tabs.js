import { ref, watch } from "vue";
import { defineStore } from "pinia";
export const useChatTabs = defineStore("chat-tabs", () => {
  function restore(userId) {
    try {
      const saved = JSON.parse(
        sessionStorage.getItem(`frangizer.chat-tabs.${userId}`),
      );
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
  const userId = ref(null),
    tabs = ref([]),
    activeId = ref(null);
  function activate(id) {
    if (!id || userId.value === id) return;
    const saved = restore(id);
    userId.value = id;
    tabs.value = saved.tabs;
    activeId.value = tabs.value.some((t) => t.id === saved.activeId)
      ? saved.activeId
      : tabs.value[0].id;
    voiceActive.value = false;
    persist();
  }
  function clear() {
    userId.value = null;
    tabs.value = [];
    activeId.value = null;
    voiceActive.value = false;
  }
  const voiceActive = ref(false);
  function persist() {
    if (!userId.value || !tabs.value.length) return;
    sessionStorage.setItem(
      `frangizer.chat-tabs.${userId.value}`,
      JSON.stringify({ tabs: tabs.value, activeId: activeId.value }),
    );
  }
  watch([tabs, activeId], persist, { deep: true });
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
    sessionStorage.removeItem(`frangizer.question-draft.${userId.value}.${id}`);
    tabs.value.splice(index, 1);
    if (!tabs.value.length) newChat();
    else if (activeId.value === id)
      activeId.value = tabs.value[Math.min(index, tabs.value.length - 1)].id;
    persist();
  }

  function select(id) {
    if (!voiceActive.value && tabs.value.some((t) => t.id === id))
      activeId.value = id;
  }
  return {
    tabs,
    activeId,
    voiceActive,
    activate,
    clear,
    newChat,
    closeChat,
    select,
  };
});
