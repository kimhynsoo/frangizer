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
  {
    role: "prospect",
    name: "예비 점주",
    account: isMock ? "demo-prospect" : "demo-prospect-01",
  },
  { role: "owner", name: "운영 점주", account: "demo-owner-a001" },
  {
    role: "hq",
    name: "본사 담당자",
    account: isMock ? "demo-hq" : "demo-hq-01",
  },
];
function fill(profile) {
  username.value = profile.account;
  password.value = isMock ? "demo-password" : "";
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
        ><span>프랜자이저<small>FRANGIZER OPERATIONS</small></span>
      </div>
      <div class="story-content">
        <span class="story-kicker">FRANCHISE OPERATIONS PLATFORM</span>
        <h1>가맹점주의 곁에서, <br />든든한 운영 파트너</h1>
        <p class="story-description">
          본사의 운영 기준과 현장의 질문을 하나의 업무 흐름으로 연결합니다.
        </p>
        <div class="service-modules" aria-label="프랜자이저 주요 기능">
          <div>
            <span>01</span>
            <strong>운영 지식</strong>
            <small>승인된 문서를 기준으로 확인</small>
          </div>
          <div>
            <span>02</span>
            <strong>현장 문의</strong>
            <small>근거와 함께 필요한 답변 제공</small>
          </div>
          <div>
            <span>03</span>
            <strong>변경 관리</strong>
            <small>공지 배포부터 점포 확인까지</small>
          </div>
        </div>
      </div>
    </section>
    <section class="login-form-side">
      <div class="login-form-wrap">
        <span class="eyebrow">WELCOME BACK</span>
        <h2>업무 공간에 로그인</h2>
        <p class="muted">발급받은 계정으로 시작하세요</p>
        <div class="demo-login">
          <p>
            {{
              isMock
                ? "역할을 선택하면 시연 계정이 입력됩니다."
                : "역할을 선택하면 테스트 계정 아이디가 입력됩니다. 비밀번호는 직접 입력하세요"
            }}
          </p>
          <div class="segmented">
            <button
              v-for="profile in profiles"
              type="button"
              :key="profile.role"
              :class="{ selected: username === profile.account }"
              :disabled="busy"
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
          계정 발급 및 접근 권한은 본사 담당자에게 문의하세요
          <br />본사 담당자 연락처: 010-XXXX-XXXX
        </p>
      </div>
      <small class="login-copyright">프랜자이저 · frangizer</small>
    </section>
  </div>
</template>
