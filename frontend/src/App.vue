<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  MessageSquare,
  CircleHelp,
  FolderOpen,
  Bell,
  ChartNoAxesCombined,
  ArrowUpRight,
  LogOut,
  Menu,
  ShieldCheck,
  ChevronDown,
} from "@lucide/vue";
import ChatNavigation from "./components/ChatNavigation.vue";
import { useChatTabs } from "./stores/chat-tabs";
import BrandWorkspace from "./components/BrandWorkspace.vue";
import ConnectionMark from "./components/ConnectionMark.vue";
import { useAuth } from "./stores/auth";
import { useNotices } from "./stores/notices";
import { isMock } from "./api/client";
const auth = useAuth(),
  notices = useNotices(),
  chats = useChatTabs(),
  route = useRoute(),
  router = useRouter(),
  menu = ref(false),
  chatExpanded = ref(false);
const roleNames = {
  prospect: "예비 가맹점주",
  owner: "운영 점주",
  hq: "본사 운영 담당자",
};
const links = computed(() => [
  ...(auth.can("rag:ask")
    ? [{ path: "/ask", label: "질문하기", icon: MessageSquare }]
    : []),
  ...(auth.user?.role === "prospect" && auth.can("rag:ask")
    ? [{ path: "/faq", label: "자주 묻는 질문", icon: CircleHelp }]
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
  async (id) => {
    chatExpanded.value = false;
    if (id) chats.activate(id);
    else chats.clear();
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
  <div
    v-else
    class="workspace"
    :class="{ 'chat-workspace': route.path === '/ask' }"
  >
    <a class="skip-link" href="#main-content">본문으로 이동</a>
    <button
      v-if="menu"
      class="sidebar-scrim"
      aria-label="메뉴 닫기"
      @click="menu = false"
    ></button>
    <aside class="sidebar" :class="{ open: menu }">
      <RouterLink class="brand" to="/"
        ><span class="brand-mark"><ConnectionMark :size="23" /></span
        ><span>프랜자이저<small>FRANGIZER WORKSPACE</small></span></RouterLink
      >
      <BrandWorkspace
        :key="auth.user?.brand_id"
        :name="isMock ? '가맹카페 A' : auth.user?.brand_id || '가맹 브랜드'"
        :monogram="isMock ? 'A' : ''"
        :caption="isMock ? '가상 프랜차이즈 · 시연 기업' : '브랜드 업무 공간'"
      >
        <div v-for="link in links" :key="link.path" class="nav-item">
          <div class="nav-item-row">
            <RouterLink
              :to="link.path"
              class="nav-link"
              @click="link.path === '/ask' && (chatExpanded = !chatExpanded)"
              ><component :is="link.icon" :size="19" /><span>{{
                link.label
              }}</span
              ><span
                v-if="
                  auth.user?.role === 'owner' &&
                  link.path === '/notices' &&
                  notices.unread
                "
                class="nav-count"
                >{{ notices.unread }}</span
              ></RouterLink
            >
            <button
              v-if="link.path === '/ask'"
              type="button"
              class="nav-chat-toggle"
              :aria-expanded="chatExpanded"
              aria-controls="ask-conversations"
              :aria-label="chatExpanded ? '대화 목록 접기' : '대화 목록 펼치기'"
              @click="chatExpanded = !chatExpanded"
            >
              <ChevronDown :size="16" :class="{ expanded: chatExpanded }" />
            </button>
          </div>
          <ChatNavigation
            v-if="link.path === '/ask'"
            v-show="chatExpanded"
            id="ask-conversations"
            @navigate="menu = false"
          />
        </div>
      </BrandWorkspace>
      <div class="sidebar-bottom">
        <div class="sidebar-note">
          <ShieldCheck :size="20" /><strong
            >근거가 있는 안내, <br />더 명확한 매장 운영</strong
          >
          <p>승인된 문서를 바탕으로<br />필요한 기준을 확인하세요</p>
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
          <div
            class="user-row topbar-account"
            role="group"
            aria-label="로그인 계정"
          >
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
      </header>
      <main id="main-content" class="main-content">
        <RouterView v-if="auth.authenticated" :key="auth.user?.id" />
      </main>
      <footer class="workspace-footer">
        <span>프랜자이저 · 가맹카페 A 업무 공간</span
        ><span>{{
          isMock
            ? "합성 시연 데이터 · 새로고침하면 시연 변경이 초기화됩니다."
            : "등록·승인된 문서의 근거를 확인해 주세요"
        }}</span>
      </footer>
    </div>
    <nav class="mobile-bottom-nav" aria-label="모바일 주요 메뉴">
      <RouterLink v-for="link in links" :key="link.path" :to="link.path">
        <component :is="link.icon" :size="21" aria-hidden="true" />
        <span>{{ link.label }}</span>
        <span
          v-if="
            auth.user?.role === 'owner' &&
            link.path === '/notices' &&
            notices.unread
          "
          class="mobile-unread"
          >{{ notices.unread }}</span
        >
      </RouterLink>
    </nav>
  </div>
</template>
