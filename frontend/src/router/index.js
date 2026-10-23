import { createRouter, createWebHistory } from "vue-router";
import { useAuth } from "../stores/auth";
const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/login",
      name: "login",
      component: () => import("../views/LoginView.vue"),
    },
    { path: "/", redirect: "/ask" },
    {
      path: "/ask",
      name: "ask",
      component: () => import("../views/AskView.vue"),
      meta: { permission: "rag:ask", title: "질문하기" },
    },
    {
      path: "/faq",
      name: "faq",
      component: () => import("../views/FaqView.vue"),
      meta: {
        roles: ["prospect"],
        permission: "rag:ask",
        title: "자주 묻는 질문",
      },
    },
    {
      path: "/documents",
      name: "documents",
      component: () => import("../views/DocumentsView.vue"),
      meta: { title: "문서 보관함" },
    },
    {
      path: "/notices",
      name: "notices",
      component: () => import("../views/NoticesView.vue"),
      meta: { roles: ["owner", "hq"], title: "변경 공지" },
    },
    {
      path: "/dashboard",
      name: "dashboard",
      component: () => import("../views/HqDashboardView.vue"),
      meta: {
        roles: ["hq"],
        permission: "analytics:read",
        title: "문의 대시보드",
      },
    },
    {
      path: "/forbidden",
      component: () => import("../views/ForbiddenView.vue"),
      meta: { title: "접근 제한" },
    },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
});
router.beforeEach(async (to) => {
  const auth = useAuth();
  await auth.restore();
  if (!auth.authenticated && to.name !== "login")
    return { name: "login", query: { redirect: to.fullPath } };
  const home = () =>
    auth.can("analytics:read")
      ? "/dashboard"
      : auth.can("rag:ask")
        ? "/ask"
        : "/documents";
  if (auth.authenticated && to.name === "login") return home();
  if (
    (to.meta.roles && !to.meta.roles.includes(auth.user?.role)) ||
    (to.meta.permission && !auth.can(to.meta.permission))
  )
    return "/forbidden";
});
export default router;
