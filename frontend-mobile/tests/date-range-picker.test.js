// @vitest-environment happy-dom
import { mount } from "@vue/test-utils";
import { afterEach, expect, it } from "vitest";
import DateRangePicker from "../src/components/DateRangePicker.vue";
let wrapper;
afterEach(() => { wrapper?.unmount(); document.body.innerHTML = ""; });
async function open() {
 wrapper = mount(DateRangePicker, { props: { start: "2026-10-01", end: "2026-10-05" }, global: { stubs: { teleport: true }, directives: { "focus-trap": {} } } });
 await wrapper.find(".range-trigger").trigger("click");
}
it("같은 달력에서 선택한 두 날짜를 완료할 때만 반영한다", async () => {
 await open();
 await wrapper.find('[aria-label="2026-10-06"]').trigger("click");
 expect(wrapper.emitted("update:start")).toBeUndefined();
 expect(wrapper.find(".range-apply").element.disabled).toBe(true);
 await wrapper.find('[aria-label="2026-10-09"]').trigger("click");
 expect(wrapper.find('[aria-label="2026-10-08"]').classes()).toContain("between");
 await wrapper.find(".range-apply").trigger("click");
 expect(wrapper.emitted("update:start")[0]).toEqual(["2026-10-06"]);
 expect(wrapper.emitted("update:end")[0]).toEqual(["2026-10-09"]);
});
it("종료일을 먼저 골라도 날짜순으로 정렬한다", async () => {
 await open();
 await wrapper.find('[aria-label="2026-10-09"]').trigger("click");
 await wrapper.find('[aria-label="2026-10-06"]').trigger("click");
 await wrapper.find(".range-apply").trigger("click");
 expect(wrapper.emitted("update:start")[0]).toEqual(["2026-10-06"]);
 expect(wrapper.emitted("update:end")[0]).toEqual(["2026-10-09"]);
});
it("취소하면 기존 기간을 유지한다", async () => {
 await open();
 await wrapper.find('[aria-label="2026-10-06"]').trigger("click");
 await wrapper.find('[aria-label="기간 선택 닫기"]').trigger("click");
 expect(wrapper.emitted("update:start")).toBeUndefined();
});
