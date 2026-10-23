// @vitest-environment happy-dom
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
const m = vi.hoisted(() => ({ mock: true, login: vi.fn(), replace: vi.fn() }));
vi.mock("../src/api/client", () => ({
  get isMock() {
    return m.mock;
  },
}));
vi.mock("../src/stores/auth", () => ({
  useAuth: () => ({ login: m.login, can: () => true }),
}));
vi.mock("vue-router", () => ({
  useRouter: () => ({ replace: m.replace }),
  useRoute: () => ({ query: {} }),
}));
import LoginView from "../src/views/LoginView.vue";
let wrapper;
beforeEach(() => {
  vi.clearAllMocks();
  m.mock = true;
  m.login.mockResolvedValue();
});
afterEach(() => wrapper?.unmount());
const select = async (name) =>
  wrapper
    .findAll("button")
    .find((b) => b.text() === name)
    .trigger("click");
it("실제 API 모드에서도 역할 버튼이 보이고 서버 시드 계정을 선택한다", async () => {
  m.mock = false;
  wrapper = mount(LoginView);
  for (const [name, account] of [
    ["예비 점주", "demo-prospect-01"],
    ["운영 점주", "demo-owner-a001"],
    ["본사 담당자", "demo-hq-01"],
  ]) {
    await select(name);
    expect(wrapper.get('input[name="username"]').element.value).toBe(account);
    expect(wrapper.get('input[name="password"]').element.value).toBe("");
  }
  expect(wrapper.text()).toContain("비밀번호는 직접 입력하세요");
});
it("실제 API 로그인은 사용자가 입력한 비밀번호를 전달하고 역할 전환은 비밀번호를 지운다", async () => {
  m.mock = false;
  wrapper = mount(LoginView);
  await select("본사 담당자");
  await wrapper.get('input[name="password"]').setValue("test-entered-password");
  await wrapper.get("form").trigger("submit");
  await flushPromises();
  expect(m.login).toHaveBeenCalledWith({
    username: "demo-hq-01",
    password: "test-entered-password",
  });
  await select("예비 점주");
  expect(wrapper.get('input[name="password"]').element.value).toBe("");
});
it("샘플 모드의 계정·비밀번호 자동 입력은 유지한다", async () => {
  wrapper = mount(LoginView);
  await select("예비 점주");
  expect(wrapper.get('input[name="username"]').element.value).toBe(
    "demo-prospect",
  );
  expect(wrapper.get('input[name="password"]').element.value).toBe(
    "demo-password",
  );
  await select("본사 담당자");
  expect(wrapper.get('input[name="username"]').element.value).toBe("demo-hq");
});
