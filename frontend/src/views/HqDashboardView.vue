<script setup>
import { ref, computed, onMounted } from "vue";
import {
  ArrowUpRight,
  MessageSquare,
  CircleCheck,
  CircleHelp,
  Store,
  RefreshCw,
  SlidersHorizontal,
} from "@lucide/vue";
import * as api from "../api/analytics";
import { useAuth } from "../stores/auth";
import ErrorNotice from "../components/ErrorNotice.vue";
import StatusBadge from "../components/StatusBadge.vue";
import PageNav from "../components/PageNav.vue";
import AnswerDetails from "../components/AnswerDetails.vue";
import SourcePanel from "../components/SourcePanel.vue";
const auth = useAuth(),
  data = ref(null),
  rows = ref([]),
  meta = ref(null),
  busy = ref(false),
  error = ref(null),
  regions = ref([]),
  stores = ref([]),
  topics = ref([]),
  selected = ref(null),
  selectedSource = ref(null),
  saving = ref(false),
  detailError = ref(null),
  saved = ref(""),
  edit = ref({}),
  activeQuery = ref({});
const filters = ref({
  week_start: "",
  region_code: "",
  store_id: "",
  category: "",
  topic_id: "",
  user_role: "owner",
  answer_status: "",
  resolution_status: "",
  classification_status: "",
});
const categories = {
  franchise: "가맹 준비",
  recipe: "레시피",
  hygiene: "위생",
  consumer: "고객 대응",
  other: "기타",
};
const visibleStores = computed(() =>
  stores.value.filter(
    (s) =>
      !filters.value.region_code || s.region_code === filters.value.region_code,
  ),
);
const visibleTopics = computed(() =>
  topics.value.filter(
    (t) => !filters.value.category || t.category === filters.value.category,
  ),
);
const editTopics = computed(() =>
  topics.value.filter((t) => t.category === edit.value.category),
);
const date = (value) =>
  new Date(value).toLocaleDateString("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "long",
    day: "numeric",
  });
const percent = (value) =>
  value === null || value === undefined ? "N/A" : `${Math.round(value * 100)}%`;
let generation = 0;
async function allOptions(kind) {
  let page = 1,
    all = [];
  while (true) {
    const r = await api.options(kind, { page, page_size: 100 });
    all.push(...r.data);
    if (page >= r.meta.total_pages) return all;
    page++;
  }
}
async function load(page = 1, apply = true) {
  if (
    apply &&
    filters.value.week_start &&
    new Date(`${filters.value.week_start}T00:00:00+09:00`).getUTCDay() !== 0
  ) {
    error.value = { message: "주 시작일은 월요일로 선택해 주세요." };
    return;
  }
  // KST Monday midnight is UTC Sunday. API keeps all calendar boundaries in KST.
  const current = ++generation;
  busy.value = true;
  error.value = null;
  if (apply)
    activeQuery.value = Object.fromEntries(
      Object.entries(filters.value).filter(([, v]) => v !== ""),
    );
  const query = { ...activeQuery.value },
    {
      answer_status,
      resolution_status,
      classification_status,
      ...summaryQuery
    } = query;
  try {
    const [summary, list] = await Promise.all([
      api.summary(summaryQuery),
      api.events({ ...query, page, page_size: 8 }),
    ]);
    if (current === generation) {
      data.value = summary.data;
      rows.value = list.data;
      meta.value = list.meta;
    }
  } catch (e) {
    if (current === generation) error.value = e;
  } finally {
    if (current === generation) busy.value = false;
  }
}
async function open(row) {
  detailError.value = null;
  saved.value = "";
  try {
    selected.value = (await api.event(row.question_id)).data;
    edit.value = {
      category: selected.value.category,
      topic_id: selected.value.topic_id || "",
      resolution_status: selected.value.resolution_status,
      review_note: "",
    };
  } catch (e) {
    error.value = e;
  }
}
async function save() {
  saving.value = true;
  detailError.value = null;
  try {
    selected.value = (
      await api.update(selected.value.question_id, {
        expected_revision: selected.value.revision,
        category: edit.value.category,
        topic_id: edit.value.topic_id || null,
        resolution_status: edit.value.resolution_status,
        review_note: edit.value.review_note,
      })
    ).data;
    saved.value = "처리 상태를 저장했습니다.";
    await load(meta.value.page, false);
  } catch (e) {
    detailError.value = e;
  } finally {
    saving.value = false;
  }
}
onMounted(async () => {
  try {
    [regions.value, stores.value, topics.value] = await Promise.all([
      allOptions("regions"),
      allOptions("stores"),
      allOptions("topics"),
    ]);
  } catch (e) {
    error.value = e;
    return;
  }
  await load();
});
</script>
<template>
  <section>
    <div class="page-heading">
      <div>
        <span class="eyebrow">HEADQUARTERS OVERVIEW</span>
        <h1>질문을 모으면, 개선이 보입니다.</h1>
        <p>매장에서 자주 묻는 내용과 추가 확인이 필요한 문의를 살펴보세요.</p>
      </div>
      <button
        class="button secondary small"
        :disabled="busy"
        @click="load(1, false)"
      >
        <RefreshCw :size="15" />새로고침
      </button>
    </div>
    <form class="filter-bar" @submit.prevent="load()">
      <SlidersHorizontal :size="18" /><label
        >조회 주<input
          v-model="filters.week_start"
          type="date"
          aria-label="조회 주 월요일" /></label
      ><label
        >지역<select
          v-model="filters.region_code"
          @change="filters.store_id = ''"
        >
          <option value="">전체 지역</option>
          <option
            v-for="region in regions"
            :key="region.code"
            :value="region.code"
          >
            {{ region.name }}
          </option>
        </select></label
      ><label
        >점포<select v-model="filters.store_id">
          <option value="">전체 점포</option>
          <option
            v-for="store in visibleStores"
            :key="store.id"
            :value="store.id"
          >
            {{ store.name }}
          </option>
        </select></label
      ><label
        >사용자<select v-model="filters.user_role">
          <option value="owner">운영 점주</option>
          <option value="prospect">예비 점주</option>
        </select></label
      ><button class="button small" :disabled="busy">조회</button>
    </form>
    <ErrorNotice :error="error" @retry="load(1, false)" />
    <p v-if="busy" class="loading-line" role="status">집계를 불러오는 중…</p>
    <template v-if="data"
      ><div class="period-line">
        <strong
          >{{ date(data.period.start) }} —
          {{
            date(
              new Date(new Date(data.period.end_exclusive) - 1).toISOString(),
            )
          }}</strong
        ><span
          class="badge"
          :class="data.period.is_partial ? 'needs_review' : ''"
          >{{ data.period.is_partial ? "진행 중" : "완료 주" }}</span
        ><span class="muted"
          >KST ·
          {{ activeQuery.user_role === "prospect" ? "예비 점주" : "운영 점주" }}
          문의</span
        ><small class="muted"
          >{{
            new Date(data.generated_at).toLocaleTimeString("ko-KR", {
              timeZone: "Asia/Seoul",
              hour: "2-digit",
              minute: "2-digit",
            })
          }}
          기준</small
        >
      </div>
      <div class="metric-grid">
        <article class="metric-card">
          <span><MessageSquare :size="17" />전체 문의</span
          ><strong
            >{{ data.metrics.total.toLocaleString() }}<small>건</small></strong
          >
          <p>
            {{
              data.change_kind === "new"
                ? "직전 기간 0건 · 신규 문의"
                : data.total_change_rate === null
                  ? "증감률 N/A"
                  : `직전 기간 대비 ${percent(data.total_change_rate)}`
            }}
          </p>
        </article>
        <article class="metric-card">
          <span><CircleCheck :size="17" />근거 답변 비율</span
          ><strong>{{ percent(data.metrics.guidance_rate) }}</strong>
          <p>{{ data.metrics.answered_count }}건 근거 답변 · 정상 응답 기준</p>
        </article>
        <article class="metric-card warm">
          <span><CircleHelp :size="17" />추가 검토 필요</span
          ><strong
            >{{ data.metrics.review_required_count }}<small>건</small></strong
          >
          <p>
            추가 확인 {{ data.metrics.needs_review_count }} · 담당자 검토
            {{ data.metrics.handoff_count }}
          </p>
        </article>
        <article class="metric-card">
          <span><Store :size="17" />질문한 점포</span
          ><strong
            >{{ data.metrics.active_store_count }}<small>곳</small></strong
          >
          <p>
            점포당 평균
            {{
              data.metrics.questions_per_active_store === null
                ? "N/A"
                : data.metrics.questions_per_active_store.toFixed(1)
            }}건
          </p>
        </article>
      </div>
      <div class="operational-counts">
        <span
          >시스템 오류
          <strong>{{ data.metrics.system_error_count }}건</strong></span
        ><span
          >답변 처리 중
          <strong>{{ data.metrics.processing_count }}건</strong></span
        ><span
          >분류 대기
          <strong
            >{{ data.metrics.classification_pending_count }}건</strong
          ></span
        >
      </div>
      <details class="metric-definitions">
        <summary>지표는 어떻게 계산하나요?</summary>
        <p>
          전체 문의는 질문 ID 기준이며 시스템 오류·처리 중 문의도 포함합니다.
          근거 답변 비율은 근거 답변 ÷ 완료된 응답(근거 답변·추가 확인·담당자
          검토)입니다. 실제 문제 해결률이나 담당자 종결률을 의미하지 않습니다.
          추가 검토 필요는 추가 확인과 담당자 검토의 합계입니다. 분모가 0이면
          N/A로 표시합니다.
        </p>
        <p>
          상단 집계에는 기간·지역·점포·분류·주제·사용자 필터가 적용됩니다. 답변
          상태·처리 상태·분류 진행 상태는 아래 문의 목록에만 적용됩니다.
        </p>
      </details>
      <div class="dashboard-columns">
        <section class="surface topic-surface">
          <div class="surface-toolbar">
            <h2>자주 묻는 주제</h2>
            <span class="muted">문의 건수</span>
          </div>
          <article
            v-for="(topic, i) in data.top_topics"
            :key="topic.topic_id"
            class="topic-row"
          >
            <span class="topic-rank">{{ String(i + 1).padStart(2, "0") }}</span>
            <div>
              <strong>{{ topic.label }}</strong>
              <div class="bar-track">
                <span
                  :style="{
                    width: `${(topic.count / (data.top_topics[0]?.count || 1)) * 100}%`,
                  }"
                ></span>
              </div>
            </div>
            <strong>{{ topic.count }}<small>건</small></strong>
          </article>
          <div v-if="!data.top_topics.length" class="empty-state">
            이 기간에는 집계할 주제가 없습니다.
          </div>
          <p class="surface-footnote">
            질문 빈도는 문서·교육 개선을 위한 참고 지표입니다.
          </p>
        </section>
        <section class="surface region-surface">
          <div class="surface-toolbar">
            <h2>지역별 문의</h2>
            <span class="muted">점포 운영 현황</span>
          </div>
          <div
            v-for="region in data.by_region"
            :key="region.region_code"
            class="region-row"
          >
            <div>
              <strong>{{ region.region_name }}</strong
              ><span v-if="region.small_sample" class="badge">표본 적음</span>
            </div>
            <strong>{{ region.metrics.total }}<small>건</small></strong>
          </div>
          <div v-if="!data.by_region.length" class="empty-state">
            표시할 지역 집계가 없습니다.
          </div>
          <p class="surface-footnote">
            접수 당시의 점포 지역을 기준으로 집계합니다.
          </p>
        </section>
      </div>
      <section class="surface event-surface">
        <div class="surface-toolbar">
          <h2>문의 목록</h2>
          <span class="muted">개인정보를 마스킹한 질문</span>
        </div>
        <form class="event-filters" @submit.prevent="load()">
          <select
            v-model="filters.category"
            aria-label="문의 분류"
            @change="filters.topic_id = ''"
          >
            <option value="">전체 분류</option>
            <option v-for="(label, key) in categories" :value="key" :key="key">
              {{ label }}
            </option></select
          ><select v-model="filters.topic_id" aria-label="문의 주제">
            <option value="">전체 주제</option>
            <option
              v-for="topic in visibleTopics"
              :key="topic.id"
              :value="topic.id"
            >
              {{ topic.label }}
            </option></select
          ><select v-model="filters.answer_status" aria-label="답변 상태">
            <option value="">전체 답변 상태</option>
            <option value="answered">근거 확인</option>
            <option value="needs_review">추가 확인 필요</option>
            <option value="handoff">담당자 확인 필요</option>
            <option value="processing">답변 중</option>
            <option value="system_error">시스템 오류</option></select
          ><select v-model="filters.resolution_status" aria-label="처리 상태">
            <option value="">전체 처리 상태</option>
            <option value="unknown">미확인</option>
            <option value="open">접수</option>
            <option value="in_progress">처리 중</option>
            <option value="closed">종결</option></select
          ><select
            v-model="filters.classification_status"
            aria-label="분류 진행 상태"
          >
            <option value="">전체 분류 진행 상태</option>
            <option value="pending">분류 대기</option>
            <option value="classified">분류 완료</option>
            <option value="failed">분류 실패</option>
            <option value="corrected">분류 수정됨</option></select
          ><button class="button small secondary" :disabled="busy">
            필터 적용
          </button>
        </form>
        <div class="table-scroll">
          <table>
            <thead>
              <tr>
                <th>질문</th>
                <th>점포</th>
                <th>분류</th>
                <th>답변 상태</th>
                <th>처리 상태</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in rows" :key="row.question_id">
                <td class="question-cell">
                  {{ row.question_redacted
                  }}<small>{{ date(row.created_at) }}</small>
                </td>
                <td>{{ row.store_name || "예비 점주" }}</td>
                <td>{{ categories[row.category] }}</td>
                <td><StatusBadge :status="row.answer_status" /></td>
                <td><StatusBadge :status="row.resolution_status" /></td>
                <td>
                  <button
                    class="icon-button"
                    aria-label="문의 상세 보기"
                    @click="open(row)"
                  >
                    <ArrowUpRight :size="17" />
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="!rows.length && !busy" class="empty-state">
          조건에 해당하는 문의가 없습니다.
        </div>
        <PageNav
          :meta="meta"
          :busy="busy"
          @change="(page) => load(page, false)"
        /></section
    ></template>
    <div
      v-if="selected"
      class="modal-backdrop"
      @click.self="!saving && (selected = null)"
    >
      <section
        class="source-panel"
        v-focus-trap="() => !saving && (selected = null)"
        role="dialog"
        aria-modal="true"
        aria-labelledby="event-title"
      >
        <div class="panel-heading">
          <span class="eyebrow">QUESTION DETAIL</span
          ><button
            class="icon-button"
            aria-label="문의 상세 닫기"
            :disabled="saving"
            @click="selected = null"
          >
            ✕
          </button>
        </div>
        <h2 id="event-title">{{ selected.question_redacted }}</h2>
        <p class="muted">
          {{ selected.store_name || "예비 점주" }} ·
          {{ date(selected.created_at) }}
        </p>
        <StatusBadge :status="selected.answer_status" />
        <p>
          <span class="muted">질문 분류 · </span
          ><StatusBadge
            :status="
              selected.classification_status === 'failed'
                ? 'classification_failed'
                : selected.classification_status
            "
          />
        </p>
        <AnswerDetails
          v-if="selected.result"
          :result="selected.result"
          :question="selected.question_redacted"
          @source="selectedSource = $event"
        />
        <div v-else class="history-state">
          <p>
            {{
              selected.answer_status === "processing"
                ? "아직 답변을 처리하고 있습니다. 잠시 후 최신 문의를 다시 불러오세요."
                : "시스템 오류로 답변 결과가 없습니다. 근거 부족으로 처리된 것이 아닙니다."
            }}
          </p>
          <small v-if="selected.error_code"
            >오류 코드 · {{ selected.error_code }}</small
          ><button class="button small secondary" @click="open(selected)">
            최신 문의 다시 불러오기
          </button>
        </div>
        <p class="info-banner">
          답변 상태와 담당자의 처리 상태는 별도로 관리합니다.
        </p>
        <ErrorNotice :error="detailError" @retry="save" />
        <p v-if="detailError?.response?.status === 409">
          <button class="button secondary" @click="open(selected)">
            최신 문의 다시 불러오기
          </button>
        </p>
        <p v-if="saved" class="success-notice" role="status">{{ saved }}</p>
        <form @submit.prevent="save">
          <fieldset :disabled="saving || !auth.can('analytics:write')">
            <label
              >분류<select v-model="edit.category" @change="edit.topic_id = ''">
                <option
                  v-for="(label, key) in categories"
                  :key="key"
                  :value="key"
                >
                  {{ label }}
                </option>
              </select></label
            ><label
              >주제<select v-model="edit.topic_id">
                <option value="">미분류</option>
                <option
                  v-for="topic in editTopics"
                  :key="topic.id"
                  :value="topic.id"
                >
                  {{ topic.label }}
                </option>
              </select></label
            ><label
              >처리 상태<select v-model="edit.resolution_status">
                <option value="unknown">미확인</option>
                <option value="open">접수</option>
                <option value="in_progress">처리 중</option>
                <option value="closed">종결</option>
              </select></label
            ><label
              >검토 메모<textarea
                v-model="edit.review_note"
                maxlength="2000"
                rows="3"
              ></textarea></label
            ><button
              class="button"
              :disabled="saving || !auth.can('analytics:write')"
            >
              {{ saving ? "저장 중…" : "변경 저장" }}
            </button>
          </fieldset>
        </form>
      </section>
    </div>
    <SourcePanel
      v-if="selectedSource"
      :source="selectedSource"
      @close="selectedSource = null"
    />
  </section>
</template>
