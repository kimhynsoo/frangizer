<script setup>
import { ref } from "vue";
import { useRouter, useRoute } from "vue-router";
import { Sprout, ArrowRight, BookOpen, Bell, ShieldCheck } from "@lucide/vue";
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
        <span class="brand-mark"><Sprout :size="24" /></span
        ><span>프랜자이저<small>FRANGIZER WORKSPACE</small></span>
      </div>
      <div class="story-content">
        <span class="eyebrow">YOUR EVERYDAY PARTNER</span>
        <h1>매장의 질문에,<br />근거 있는 답을.</h1>
        <p>
          가맹 준비부터 매일의 운영까지.<br />흩어진 문서 속 필요한 기준을
          한곳에서 확인하세요.
        </p>
        <div class="story-features">
          <div>
            <BookOpen :size="20" /><span>문서에 기반한 답변과 출처</span>
          </div>
          <div><Bell :size="20" /><span>놓치지 않는 운영 기준 변경</span></div>
          <div>
            <ShieldCheck :size="20" /><span>역할에 맞는 안전한 정보 접근</span>
          </div>
        </div>
      </div>
      <div class="story-footer">
        가맹카페 A <span>가상 기업을 활용한 프로젝트 시연</span>
      </div>
    </section>
    <section class="login-form-side">
      <div class="login-form-wrap">
        <span class="eyebrow">WELCOME BACK</span>
        <h2>업무 공간에 로그인</h2>
        <p class="muted">발급받은 계정으로 시작하세요.</p>
        <div v-if="isMock" class="demo-login">
          <span class="badge synthetic">샘플 데이터 시연</span>
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
        </p>
      </div>
      <small class="login-copyright">프랜자이저 · frangizer</small>
    </section>
  </div>
</template>
