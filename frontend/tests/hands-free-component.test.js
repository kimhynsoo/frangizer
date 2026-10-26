// @vitest-environment happy-dom
import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import HandsFreeVoice from "../src/components/HandsFreeVoice.vue";
const mocks = vi.hoisted(() => ({
  start: vi.fn(),
  stop: vi.fn(),
  options: null,
}));
vi.mock("../src/api/voice", () => ({ recognitionAvailable: () => true }));
vi.mock("../src/api/hands-free", () => ({
  createHandsFree: (options) => {
    mocks.options = options;
    return { start: mocks.start, stop: mocks.stop };
  },
}));
let wrapper;
beforeEach(() => {
  vi.stubGlobal("isSecureContext", true);
  vi.clearAllMocks();
});
afterEach(() => {
  wrapper?.unmount();
  vi.unstubAllGlobals();
});
it("자동으로 켜지지 않고 버튼을 눌러야 대기한다", async () => {
  wrapper = mount(HandsFreeVoice, { props: { active: true, ask: vi.fn() } });
  expect(mocks.start).not.toHaveBeenCalled();
  await wrapper.find("button").trigger("click");
  expect(mocks.start).toHaveBeenCalledOnce();
  mocks.options.onState("waiting");
  await wrapper.vm.$nextTick();
  expect(wrapper.text()).toContain("음성 대기 끄기");
  await wrapper.find("button").trigger("click");
  expect(mocks.stop).toHaveBeenCalledOnce();
});
it("다른 대화로 이동하면 중지한다", async () => {
  wrapper = mount(HandsFreeVoice, { props: { active: true, ask: vi.fn() } });
  await wrapper.setProps({ active: false });
  expect(mocks.stop).toHaveBeenCalledOnce();
});
it("화면 숨김과 컴포넌트 해제 시 중지한다", () => {
  wrapper = mount(HandsFreeVoice, { props: { active: true, ask: vi.fn() } });
  Object.defineProperty(document, "hidden", {
    configurable: true,
    value: true,
  });
  document.dispatchEvent(new Event("visibilitychange"));
  expect(mocks.stop).toHaveBeenCalledOnce();
  wrapper.unmount();
  wrapper = null;
  expect(mocks.stop).toHaveBeenCalledTimes(2);
  delete document.hidden;
});
