<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  MessageSquare,
  FolderOpen,
  Bell,
  ChartNoAxesCombined,
  ArrowUpRight,
  LogOut,
  Sprout,
  Menu,
  ShieldCheck,
} from "@lucide/vue";
import { useAuth } from "./stores/auth";
import { useNotices } from "./stores/notices";
import { isMock } from "./api/client";
const auth = useAuth(),
  notices = useNotices(),
  route = useRoute(),
  router = useRouter(),
  menu = ref(false);
const roleNames = {
  prospect: "예비 가맹점주",
  owner: "운영 점주",
  hq: "본사 운영 담당자",
};
const links = computed(() => [
  ...(auth.can("rag:ask")
    ? [{ path: "/ask", label: "질문하기", icon: MessageSquare }]
    : []),
  {
    path: "/documents",
    label: auth.can("document:write") ? "문서 관리" : "문서 보관함",
    icon: FolderOpen,
  },
  ...(["owner", "hq"].includes(auth.user?.role)
    ? [{ path: "/notices", label: "변경 공지", icon: Bell }]
    : []),
  ...(auth.can("analytics:read")
    ? [
        {
          path: "/dashboard",
          label: "문의 대시보드",
          icon: ChartNoAxesCombined,
        },
      ]
    : []),
]);
function logout() {
  auth.logout();
  notices.clear();
  menu.value = false;
  router.push("/login");
}
function expired() {
  auth.logout();
  notices.clear();
  router.push({ name: "login", query: { redirect: route.fullPath } });
}
watch(
  () => auth.user?.id,
  async () => {
    notices.clear();
    if (["owner", "hq"].includes(auth.user?.role)) {
      try {
        await notices.refresh();
      } catch {
        /* Notices view exposes fetch errors. */
      }
    }
  },
);
watch(
  () => route.path,
  () => (menu.value = false),
);
watch(
  () => route.fullPath,
  async () => {
    if (["owner", "hq"].includes(auth.user?.role)) {
      try {
        await notices.refresh();
      } catch {
        /* A failed refresh must not replace the current page. */
      }
    }
  },
);
onMounted(() => window.addEventListener("session-expired", expired));
onUnmounted(() => window.removeEventListener("session-expired", expired));
</script>
<template>
  <RouterView v-if="route.name === 'login'" />
  <div v-else class="workspace">
    <a class="skip-link" href="#main-content">본문으로 이동</a>
    <button
      v-if="menu"
      class="sidebar-scrim"
      aria-label="메뉴 닫기"
      @click="menu = false"
    ></button>
    <aside class="sidebar" :class="{ open: menu }">
      <RouterLink class="brand" to="/"
        ><span class="brand-mark"><Sprout :size="23" /></span
        ><span>프랜자이저<small>FRANGIZER WORKSPACE</small></span></RouterLink
      >
      <div class="brand-switch">
        <span class="store-monogram">A</span>
        <div>
          <strong>가맹카페 A</strong><small>가상 프랜차이즈 · 시연 기업</small>
        </div>
        <span class="switch-dot"></span>
      </div>
      <div class="nav-label">WORKSPACE</div>
      <nav aria-label="주요 메뉴">
        <RouterLink
          v-for="link in links"
          :key="link.path"
          :to="link.path"
          class="nav-link"
          ><component :is="link.icon" :size="19" /><span>{{ link.label }}</span
          ><span
            v-if="link.path === '/notices' && notices.unread"
            class="nav-count"
            >{{ notices.unread }}</span
          ></RouterLink
        >
      </nav>
      <div class="sidebar-bottom">
        <div class="sidebar-note">
          <ShieldCheck :size="20" /><strong
            >근거가 있는 안내,<br />더 명확한 매장 운영.</strong
          >
          <p>승인된 문서를 바탕으로<br />필요한 기준을 확인하세요.</p>
        </div>
        <div class="user-row">
          <span class="avatar">{{
            auth.user?.role === "hq" ? "본" : "점"
          }}</span>
          <div>
            <strong>{{ roleNames[auth.user?.role] }}</strong
            ><small>{{ auth.user?.store_id || "가맹카페 A" }}</small>
          </div>
          <button
            class="icon-button"
            title="로그아웃"
            aria-label="로그아웃"
            @click="logout"
          >
            <LogOut :size="17" />
          </button>
        </div>
      </div>
    </aside>
    <div class="main-shell">
      <header class="topbar">
        <div class="breadcrumb">
          <button
            class="icon-button mobile-menu"
            aria-label="메뉴 열기"
            @click="menu = !menu"
          >
            <Menu :size="20" /></button
          ><span>업무 공간</span><span class="divider">/</span
          ><strong>{{ route.meta.title }}</strong>
        </div>
        <div class="topbar-right">
          <span class="mode-indicator" :class="{ live: !isMock }"
            ><i></i>{{ isMock ? "샘플 데이터 시연" : "API 연결 모드" }}</span
          ><span class="topbar-role">{{ roleNames[auth.user?.role] }}</span>
        </div>
      </header>
      <main id="main-content" class="main-content">
        <RouterView v-if="auth.authenticated" :key="auth.user?.id" />
      </main>
      <footer class="workspace-footer">
        <span>프랜자이저 · 가맹카페 A 업무 공간</span
        ><span>{{
          isMock
            ? "합성 시연 데이터 · 새로고침하면 시연 변경이 초기화됩니다."
            : "등록·승인된 문서의 근거를 확인해 주세요."
        }}</span>
      </footer>
    </div>
  </div>
</template>
