<script setup>
import { ref, computed, nextTick, watch, onUnmounted } from "vue";
import * as api from "../api/documents";
import { isMock } from "../api/client";
import ErrorNotice from "./ErrorNotice.vue";
import PageNav from "./PageNav.vue";
const props = defineProps({
  source: Object,
  requireRead: Boolean,
  confirmBusy: Boolean,
});
const emit = defineEmits(["close", "readComplete", "confirm"]);
const data = ref(null),
  error = ref(null),
  busy = ref(false),
  page = ref(1),
  panel = ref(null),
  readPages = ref(new Set());
const readComplete = computed(() => {
  const total = data.value?.sections_meta?.total_pages;
  return (
    !busy.value &&
    !error.value &&
    total > 0 &&
    Array.from({ length: total }, (_, i) => i + 1).every((p) =>
      readPages.value.has(p),
    )
  );
});
function checkReadEnd() {
  const el = panel.value;
  if (
    !props.requireRead ||
    busy.value ||
    error.value ||
    !data.value?.sections?.length ||
    !el ||
    el.clientHeight <= 0
  )
    return;
  if (el.scrollTop + el.clientHeight >= el.scrollHeight - 4)
    readPages.value.add(page.value);
}
watch(readComplete, (complete) => {
  if (complete) emit("readComplete", { ...props.source });
});
const originalUrl = (value) => {
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
};
const formatTime = (value) =>
  new Date(value).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" });
let generation = 0;
async function load(next = 1) {
  const current = ++generation;
  page.value = next;
  busy.value = true;
  error.value = null;
  try {
    const result = await api.detail(
      props.source.document_id,
      props.source.version_id,
      { page: next, page_size: 10 },
    );
    if (current === generation) data.value = result.data;
  } catch (e) {
    if (current === generation) error.value = e;
  } finally {
    if (current === generation) {
      busy.value = false;
      await nextTick();
      if (current === generation && panel.value) {
        panel.value.scrollTop = 0;
        checkReadEnd();
      }
    }
  }
}
async function download() {
  try {
    await api.download(
      data.value.version.file_url,
      data.value.version.file_name,
    );
  } catch (e) {
    error.value = e;
  }
}
onUnmounted(() => {
  generation++;
});
watch(
  () => props.source,
  () => {
    data.value = null;
    readPages.value = new Set();
    load();
  },
  { immediate: true },
);
</script>
<template>
  <div class="modal-backdrop" @click.self="$emit('close')">
    <section
      ref="panel"
      class="source-panel"
      @scroll="checkReadEnd"
      v-focus-trap="() => emit('close')"
      role="dialog"
      aria-modal="true"
      aria-labelledby="source-title"
      tabindex="-1"
    >
      <div class="panel-heading">
        <span class="eyebrow">DOCUMENT SOURCE</span
        ><button
          class="icon-button"
          aria-label="출처 닫기"
          autofocus
          @click="$emit('close')"
        >
          ✕
        </button>
      </div>
      <h2 id="source-title">{{ source.title || "출처 상세" }}</h2>
      <p v-if="requireRead" class="info-banner">
        문서의 모든 페이지를 끝까지 내려 확인하면 확인 완료 버튼이 활성화됩니다.
      </p>
      <ErrorNotice :error="error" @retry="load(page)" />
      <p v-if="busy" role="status">문서를 불러오는 중입니다…</p>
      <template v-if="data"
        ><div class="source-meta">
          <span class="badge">v{{ data.version.version }}</span
          ><span
            class="badge"
            :class="data.version.is_demo ? 'synthetic' : ''"
            >{{
              data.version.is_demo
                ? "합성·시연 자료"
                : data.version.material_type === "official"
                  ? "법령·고시"
                  : "정보공개서 원본(수집본)"
            }}</span
          >
        </div>
        <p class="muted">
          적용일
          {{
            new Date(data.version.effective_from).toLocaleString("ko-KR", {
              timeZone: "Asia/Seoul",
            })
          }}
        </p>
        <p
          v-if="data.version.material_type === 'official'"
          class="info-banner"
          role="status"
        >
          {{
            data.version.indexing_policy.legal_temporal_reviewed
              ? "법령 시행 시점 검수 기록이 있습니다."
              : "법령 시행 시점 미검수 · 조문별 시행일과 경과규정을 확인해 주세요."
          }}
        </p>
        <p v-if="data.version.page_count" class="muted">
          전체 PDF {{ data.version.page_count }}쪽 · 출처의 쪽수는 파일
          기준이며, 인쇄 번호와 다르면 별도로 표시합니다.
        </p>
        <p v-if="source.effective_until" class="muted">
          적용 종료
          {{
            new Date(source.effective_until).toLocaleString("ko-KR", {
              timeZone: "Asia/Seoul",
            })
          }}
          (해당 시각 미포함)
        </p>
        <h3>문서 근거</h3>
        <article
          v-for="section in data.sections"
          :key="section.chunk_id"
          class="source-excerpt"
        >
          <small
            >{{ section.page ? `PDF ${section.page}쪽` : "" }}
            {{ section.article }} · {{ section.heading }}</small
          >
          <p class="muted">
            조문·본문 적용 기간 (한국 시각):
            {{ formatTime(section.effective_from) }}부터
            <template v-if="section.effective_until"
              >{{ formatTime(section.effective_until) }} 전까지 (종료 시각
              미포함)</template
            >
            <template v-else> · 종료일 지정 없음</template>
          </p>
          <p>{{ section.text }}</p>
        </article>
        <p v-if="requireRead && !data.sections.length" class="info-banner">
          확인할 문서 본문이 없어 확인을 완료할 수 없습니다.
        </p>
        <PageNav :meta="data.sections_meta" :busy="busy" @change="load" />
        <h3>원천 출처</h3>
        <p>
          {{ data.version.original_source.title }} ·
          {{ data.version.original_source.publisher }}
        </p>
        <p class="muted">
          판본 {{ data.version.original_source.edition }} · 취득일
          {{ data.version.original_source.acquired_on }}
        </p>
        <p>{{ data.version.original_source.note }}</p>
        <a
          v-if="originalUrl(data.version.original_source.url)"
          :href="originalUrl(data.version.original_source.url)"
          target="_blank"
          rel="noopener noreferrer"
          class="button secondary small"
          >공식 원문 출처 열기</a
        >
        <p v-else class="muted">
          등록된 원문 URL이 없습니다. 출처 정보와 제공 파일을 확인해 주세요.
        </p>
        <button class="button secondary" @click="download">
          파일 내려받기
        </button>
        <p v-if="isMock" class="muted">
          초기 시연 문서는 TXT 발췌를, 직접 업로드한 문서는 선택한 원본 파일을
          내려받습니다.
        </p></template
      >
      <div v-if="requireRead" class="document-read-confirm">
        <p role="status">
          {{
            readComplete
              ? "문서를 끝까지 확인했습니다."
              : `전체 ${data?.sections_meta?.total_pages || 0}페이지 중 ${readPages.size}페이지 확인`
          }}
        </p>
        <button
          class="button"
          :disabled="!readComplete || confirmBusy"
          @click="readComplete && emit('confirm')"
        >
          {{ confirmBusy ? "저장 중…" : "확인 완료" }}
        </button>
      </div>
    </section>
  </div>
</template>
