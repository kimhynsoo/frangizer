<script setup>
import { computed, ref } from "vue";
import { CalendarDays, ChevronLeft, ChevronRight, X } from "@lucide/vue";
import { koreaToday, dateRangeQuery } from "../date-range";
const props = defineProps({ start: String, end: String });
const emit = defineEmits(["update:start", "update:end"]);
const opened = ref(false),
  start = ref(""),
  end = ref(""),
  month = ref("");
const today = computed(() => koreaToday());
function open() {
  start.value = props.start || "";
  end.value = props.end || "";
  month.value = (props.start || today.value).slice(0, 7);
  selectingEnd.value = false;
  opened.value = true;
}
const selectingEnd = ref(false);
const cells = computed(() => {
  const first = new Date(`${month.value}-01T00:00:00Z`);
  const offset = (first.getUTCDay() + 6) % 7;
  const count = new Date(
    Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0),
  ).getUTCDate();
  return Array.from({ length: Math.ceil((offset + count) / 7) * 7 }, (_, i) => {
    const day = i - offset + 1;
    return day < 1 || day > count
      ? null
      : `${month.value}-${String(day).padStart(2, "0")}`;
  });
});
const monthLabel = computed(
  () => `${month.value.slice(0, 4)}년 ${Number(month.value.slice(5))}월`,
);
function move(amount) {
  const date = new Date(`${month.value}-01T00:00:00Z`);
  date.setUTCMonth(date.getUTCMonth() + amount);
  month.value = date.toISOString().slice(0, 7);
}
function choose(day) {
  if (!selectingEnd.value) {
    start.value = day;
    end.value = "";
    selectingEnd.value = true;
  } else {
    if (day < start.value) {
      end.value = start.value;
      start.value = day;
    } else end.value = day;
    selectingEnd.value = false;
  }
}
const validation = computed(() => {
  if (!start.value || !end.value) return "";
  try {
    dateRangeQuery(start.value, end.value);
    return "";
  } catch (error) {
    return error.message;
  }
});
function apply() {
  if (!start.value || !end.value || validation.value) return;
  emit("update:start", start.value);
  emit("update:end", end.value);
  opened.value = false;
}
</script>
<template>
  <button
    type="button"
    class="range-trigger"
    aria-haspopup="dialog"
    :aria-expanded="opened"
    @click="open"
  >
    <span>{{
      props.start && props.end
        ? `${props.start.replaceAll("-", ". ")} ~ ${props.end.replaceAll("-", ". ")}`
        : "기간 선택"
    }}</span>
    <CalendarDays :size="17" aria-hidden="true" />
  </button>
  <Teleport to="body">
    <div v-if="opened" class="range-backdrop" @click.self="opened = false">
      <section
        class="range-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="조회 기간 선택"
        v-focus-trap="() => (opened = false)"
      >
        <header>
          <strong>조회 기간</strong
          ><button
            type="button"
            class="icon-button"
            aria-label="기간 선택 닫기"
            @click="opened = false"
          >
            <X :size="20" />
          </button>
        </header>
        <p aria-live="polite">
          {{
            selectingEnd ? "종료일을 선택해 주세요" : "시작일을 선택해 주세요"
          }}
        </p>
        <div class="range-selection">
          <span>{{ start || "시작일" }}</span
          ><span>~</span><span>{{ end || "종료일" }}</span>
        </div>
        <div class="range-month">
          <button
            type="button"
            class="icon-button"
            aria-label="이전 달"
            @click="move(-1)"
          >
            <ChevronLeft :size="20" /></button
          ><strong>{{ monthLabel }}</strong
          ><button
            type="button"
            class="icon-button"
            aria-label="다음 달"
            :disabled="month >= today.slice(0, 7)"
            @click="move(1)"
          >
            <ChevronRight :size="20" />
          </button>
        </div>
        <div class="range-calendar">
          <span
            v-for="name in ['월', '화', '수', '목', '금', '토', '일']"
            :key="name"
            class="range-weekday"
            >{{ name }}</span
          >
          <template v-for="(day, i) in cells" :key="i">
            <button
              v-if="day"
              type="button"
              class="range-day"
              :class="{
                between: start && end && day > start && day < end,
                endpoint: day === start || day === end,
              }"
              :aria-label="day"
              :aria-pressed="
                !!(start && end && day >= start && day <= end) || day === start
              "
              :disabled="day > today"
              @click="choose(day)"
            >
              {{ Number(day.slice(-2)) }}
            </button>
            <span v-else></span>
          </template>
        </div>
        <p
          class="range-help"
          :class="{ invalid: validation }"
          aria-live="polite"
        >
          {{ validation || "시작일과 종료일을 포함해 최대 31일 조회" }}
        </p>
        <button
          type="button"
          class="button range-apply"
          :disabled="!start || !end || !!validation"
          @click="apply"
        >
          기간 선택 완료
        </button>
      </section>
    </div>
  </Teleport>
</template>
<style scoped>
.range-trigger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  min-height: 36px;
  padding: 7px 10px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--paper);
  color: var(--ink);
  font-size: 11px;
  text-align: left;
}
.range-trigger svg {
  flex-shrink: 0;
}
.range-backdrop {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: #102c2466;
}
.range-dialog {
  width: min(360px, 100%);
  max-height: 90dvh;
  overflow-y: auto;
  background: var(--paper);
  padding: 20px;
  border: 1px solid var(--line);
  border-radius: 18px;
  box-shadow: 0 20px 80px #102c2426;
  color: var(--ink);
}
.range-dialog header,
.range-month {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.range-dialog header strong {
  font-size: 18px;
}
.range-dialog p {
  margin: 8px 0 14px;
  font-size: 12px;
  color: var(--muted);
}
.range-selection {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  padding: 12px;
  border-radius: 10px;
  background: var(--sage);
  font-size: 13px;
  font-weight: 600;
}
.range-month {
  margin: 14px 0 8px;
  font-size: 14px;
}
.range-calendar {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  row-gap: 6px;
}
.range-weekday {
  text-align: center;
  color: var(--muted);
  font-size: 11px;
  padding: 8px 0;
}
.range-day {
  height: 40px;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: var(--ink);
  font-size: 14px;
}
.range-day.between {
  background: var(--sage);
}
.range-day.endpoint {
  background: var(--green);
  color: white;
  border-radius: 10px;
  font-weight: 700;
}
.range-day:focus-visible {
  outline-offset: -3px;
}
.range-dialog .range-help {
  margin-top: 16px;
  font-size: 11px;
}
.range-dialog .invalid {
  color: #883e30;
}
.range-apply {
  width: 100%;
  min-height: 44px;
}
@media (max-width: 620px) {
  .range-trigger {
    min-height: 44px;
    font-size: 13px;
  }
}
</style>
