<script setup>
import { ref } from "vue";
import { useRouter, useRoute } from "vue-router";
import { ArrowRight } from "@lucide/vue";
import ConnectionMark from "../components/ConnectionMark.vue";
import { useAuth } from "../stores/auth";
import { isMock } from "../api/client";
import ErrorNotice from "../components/ErrorNotice.vue";
const auth = useAuth(),
  router = useRouter(),
  route = useRoute(),
  username = ref(""),
  password = ref(""),
  busy = ref(false),
  error = ref(null);
const profiles = [
  { role: "owner", name: "운영 점주", account: "demo-owner-a001" },
  { role: "prospect", name: "예비 점주", account: "demo-prospect" },
  { role: "hq", name: "본사 담당자", account: "demo-hq" },
];
function fill(profile) {
  username.value = profile.account;
  password.value = "demo-password";
  error.value = null;
}
async function submit() {
  busy.value = true;
  error.value = null;
  try {
    await auth.login({
      username: username.value.trim(),
      password: password.value,
    });
    const redirect = route.query.redirect;
    await router.replace(
      typeof redirect === "string" &&
        redirect.startsWith("/") &&
        !redirect.startsWith("//") &&
        redirect !== "/login"
        ? redirect
        : auth.can("analytics:read")
          ? "/dashboard"
          : auth.can("rag:ask")
            ? "/ask"
            : "/documents",
    );
  } catch (e) {
    error.value = e;
  } finally {
    busy.value = false;
  }
}
</script>
<template>
  <div class="login-page">
    <section class="login-story">
      <div class="brand light">
        <span class="brand-mark"><ConnectionMark :size="24" /></span
        ><span>프랜자이저<small>FRANGIZER WORKSPACE</small></span>
      </div>
      <div class="story-content">
        <h1>가맹점주의 곁에서,<br />든든한 운영 파트너.</h1>
      </div>
    </section>
    <section class="login-form-side">
      <div class="login-form-wrap">
        <span class="eyebrow">WELCOME BACK</span>
        <h2>업무 공간에 로그인</h2>
        <p class="muted">발급받은 계정으로 시작하세요.</p>
        <div v-if="isMock" class="demo-login">
          <p>역할을 선택하면 시연 계정이 입력됩니다.</p>
          <div class="segmented">
            <button
              v-for="profile in profiles"
              :key="profile.role"
              :class="{ selected: username === profile.account }"
              @click="fill(profile)"
            >
              {{ profile.name }}
            </button>
          </div>
        </div>
        <ErrorNotice :error="error" @retry="submit" />
        <form @submit.prevent="submit">
          <label
            >아이디<input
              v-model="username"
              name="username"
              autocomplete="username"
              required
              minlength="3"
              maxlength="50"
              placeholder="아이디를 입력하세요"
              :disabled="busy" /></label
          ><label
            >비밀번호<input
              v-model="password"
              name="password"
              type="password"
              autocomplete="current-password"
              required
              maxlength="128"
              placeholder="비밀번호를 입력하세요"
              :disabled="busy" /></label
          ><button class="button login-submit" :disabled="busy">
            {{ busy ? "로그인 중…" : "로그인" }}<ArrowRight :size="18" />
          </button>
        </form>
        <p class="login-help">
          계정 발급 및 접근 권한은 본사 담당자에게 문의하세요.
          <br />본사 담당자 연락처: 010-XXXX-XXXX
        </p>
      </div>
      <small class="login-copyright">프랜자이저 · frangizer</small>
    </section>
  </div>
</template>
