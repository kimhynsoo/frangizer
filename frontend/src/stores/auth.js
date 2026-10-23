import { defineStore } from "pinia";
import { ref, computed } from "vue";
import * as api from "../api/auth";
import { tokenStorage } from "../api/client";
export const useAuth = defineStore("auth", () => {
  const user = ref(null);
  const checked = ref(false);
  const authenticated = computed(() => !!user.value);
  const can = (permission) => user.value?.permissions.includes(permission);
  async function restore() {
    if (checked.value) return;
    if (tokenStorage.get()) {
      try {
        user.value = (await api.me()).data;
      } catch {
        tokenStorage.clear();
      }
    }
    checked.value = true;
  }
  async function login(credentials) {
    const response = await api.login(credentials);
    tokenStorage.set(response.accessToken);
    try {
      user.value = (await api.me()).data;
      checked.value = true;
    } catch (error) {
      tokenStorage.clear();
      throw error;
    }
  }
  function logout() {
    user.value = null;
    tokenStorage.clear();
    checked.value = true;
  }
  return { user, authenticated, can, restore, login, logout };
});
