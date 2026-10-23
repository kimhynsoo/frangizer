import { defineStore } from "pinia";
import { ref } from "vue";
import * as api from "../api/notices";
export const useNotices = defineStore("notices", () => {
  const unread = ref(null);
  async function refresh() {
    unread.value = (await api.list({ unread: true, page_size: 1 })).meta.total;
  }
  function clear() {
    unread.value = null;
  }
  return { unread, refresh, clear };
});
