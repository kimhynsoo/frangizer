// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
const m = vi.hoisted(() => ({ listen: vi.fn() }));
vi.mock("../src/api/voice", () => ({ browserListen: m.listen }));
import WakeEnrollment from "../src/components/WakeEnrollment.vue";
import { loadWakeProfile } from "../src/api/wake-profile";
let wrapper;
beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
});
afterEach(() => wrapper?.unmount());
function open() {
  wrapper = mount(WakeEnrollment, {
    props: { profileKey: "live:owner" },
    global: { stubs: { teleport: true }, directives: { "focus-trap": {} } },
  });
}
const button = (text) =>
  wrapper.findAll("button").find((b) => b.text().includes(text));
async function collect(text) {
  m.listen.mockResolvedValueOnce(text);
  await button("발음 듣기").trigger("click");
  await flushPromises();
}
it("3회 수집해도 체크·저장 전에는 계정에 반영하지 않는다", async () => {
  open();
  await collect("쉬 리");
  await collect("쉬리");
  await wrapper.find("input").setValue(true);
  expect(button("선택한 발음 저장").element.disabled).toBe(true);
  await collect("씨리");
  expect(loadWakeProfile("live:owner")).toEqual([]);
  await button("선택한 발음 저장").trigger("click");
  expect(loadWakeProfile("live:owner")).toEqual(["쉬리"]);
  expect(wrapper.emitted("saved")[0][0]).toEqual(["쉬리"]);
  expect(wrapper.emitted("close")).toBeTruthy();
});
it("수집 중 닫으면 마이크를 취소하고 늦은 결과는 저장하지 않는다", async () => {
  let resolve, signal;
  m.listen.mockImplementationOnce((options) => {
    signal = options.signal;
    return new Promise((r) => (resolve = r));
  });
  open();
  await button("발음 듣기").trigger("click");
  await wrapper.find('[aria-label="발음 등록 닫기"]').trigger("click");
  expect(signal.aborted).toBe(true);
  resolve("씨리");
  await flushPromises();
  expect(wrapper.findAll("input")).toHaveLength(0);
  expect(loadWakeProfile("live:owner")).toEqual([]);
});
