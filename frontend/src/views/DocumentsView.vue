<script setup>
import { ref, onMounted, onUnmounted } from "vue";
import {
  Plus,
  FileText,
  ArrowUpRight,
  UploadCloud,
  FolderOpen,
} from "@lucide/vue";
import { useAuth } from "../stores/auth";
import * as api from "../api/documents";
import { newKey, isMock } from "../api/client";
import StatusBadge from "../components/StatusBadge.vue";
import ErrorNotice from "../components/ErrorNotice.vue";
import SourcePanel from "../components/SourcePanel.vue";
import PageNav from "../components/PageNav.vue";
const auth = useAuth(),
  currentVersions = ref({}),
  rows = ref([]),
  meta = ref(null),
  busy = ref(false),
  error = ref(null),
  category = ref(""),
  source = ref(null),
  showUpload = ref(false),
  saving = ref(false),
  success = ref(""),
  selected = ref(null),
  history = ref([]),
  historyMeta = ref(null),
  historyPage = ref(1),
  historyBusy = ref(false),
  historyError = ref(null),
  jobs = ref({}),
  file = ref(null),
  uploadKey = ref(null),
  retry = ref(null);
const management = auth.can("document:write");
const actionRequest = ref(null),
  reviewNote = ref(""),
  noticeTitle = ref(""),
  checks = ref([false, false, false, false]);
const checkLabels = [
  "원문 내용과 계량·단위를 확인했습니다.",
  "출처와 합성·공식 자료 구분을 확인했습니다.",
  "판본과 적용일을 확인했습니다.",
  "열람 대상 역할을 확인했습니다.",
];
function application(version) {
  return api.applicationStatus(
    version,
    currentVersions.value[version.document_id],
  );
}
async function currentSelection() {
  const selection = {};
  let page = 1;
  while (true) {
    const result = await api.list({
      page,
      page_size: 100,
      as_of: new Date().toISOString(),
      ...(category.value ? { category: category.value } : {}),
    });
    for (const row of result.data)
      selection[row.document_id] = row.selected_version_id;
    if (page >= result.meta.total_pages) break;
    page++;
  }
  return selection;
}
function beginAction(version, action) {
  actionRequest.value = { version, action };
  reviewNote.value = "";
  noticeTitle.value = `${version.title} v${version.version} 적용 안내`;
  checks.value = [false, false, false, false];
  error.value = null;
}
function submitAction() {
  const { version, action } = actionRequest.value;
  if (
    action === "approve" &&
    (!checks.value.every(Boolean) || !reviewNote.value.trim())
  )
    return;
  act({
    version,
    action,
    key: newKey(),
    review_note: reviewNote.value.trim(),
    notice_title: noticeTitle.value.trim(),
  });
}

const uploadFormKey = ref(0);
const form = ref({
  title: "",
  version: "",
  document_id: "",
  category: "recipe",
  material_type: "synthetic",
  visibility: "owner",
  effective_from: "",
  change_summary: "",
  publisher: "",
  source_title: "",
  source_url: "",
  edition: "",
  acquired_on: "",
  note: "",
  include_pages: "",
  exclude_pages: "",
  legal_temporal_reviewed: false,
});
const categories = {
  franchise: "가맹 준비",
  recipe: "레시피",
  hygiene: "위생",
  consumer: "고객 대응",
  other: "기타",
};
const timers = new Set();
let disposed = false,
  loadGeneration = 0,
  historyGeneration = 0;
async function load(page = 1) {
  const generation = ++loadGeneration;
  busy.value = true;
  error.value = null;
  try {
    const result = await api.list({
      page,
      page_size: 10,
      ...(category.value ? { category: category.value } : {}),
      ...(management ? { management: true } : {}),
    });
    if (generation === loadGeneration) {
      rows.value = result.data;
      meta.value = result.meta;
      if (management) {
        const selection = await currentSelection();
        if (generation === loadGeneration)
          currentVersions.value = Object.fromEntries(
            result.data.map((row) => [
              row.document_id,
              selection[row.document_id] || null,
            ]),
          );
      } else
        currentVersions.value = Object.fromEntries(
          result.data.map((row) => [row.document_id, row.selected_version_id]),
        );
    }
  } catch (e) {
    error.value = e;
    retry.value = () => load(page);
  } finally {
    if (generation === loadGeneration) busy.value = false;
  }
}
function closeHistory() {
  if (saving.value) return;
  historyGeneration++;
  selected.value = null;
  history.value = [];
  historyMeta.value = null;
  historyBusy.value = false;
  historyError.value = null;
}
async function openHistory(row, page = 1) {
  if (!row || disposed) return;
  const generation = ++historyGeneration;
  selected.value = row;
  historyPage.value = page;
  history.value = [];
  historyMeta.value = null;
  historyBusy.value = true;
  historyError.value = null;
  try {
    const result = await api.versions(row.document_id, { page, page_size: 10 });
    if (disposed || generation !== historyGeneration) return;
    history.value = result.data;
    historyMeta.value = result.meta;
  } catch (e) {
    if (!disposed && generation === historyGeneration) historyError.value = e;
  } finally {
    if (!disposed && generation === historyGeneration)
      historyBusy.value = false;
  }
}
function startUpload() {
  showUpload.value = true;
  uploadKey.value = null;
  error.value = null;
  success.value = "";
}
async function fillRecipeDemo() {
  if (saving.value) return;
  const { uploadPreset } = await import("../demo/materials.js");
  form.value = uploadPreset();
  file.value = null;
  uploadKey.value = null;
  uploadFormKey.value++;
  error.value = null;
}
function chooseFile(event) {
  file.value = event.target.files[0] || null;
  uploadKey.value = null;
}
async function upload(reuse = false) {
  if (saving.value) return;
  error.value = null;
  if (
    !file.value ||
    !/\.(pdf|txt|md|markdown)$/i.test(file.value.name) ||
    file.value.size > 10 * 1024 * 1024
  ) {
    error.value = {
      message: "PDF·TXT·Markdown 파일을 10MB 이하로 선택해 주세요",
    };
    return;
  }
  let pages;
  try {
    pages = api.pagePolicy(form.value.include_pages, form.value.exclude_pages);
  } catch (e) {
    error.value = { message: e.message };
    return;
  }
  const metadata = {
    title: form.value.title.trim(),
    version: form.value.version.trim(),
    ...(form.value.document_id.trim()
      ? { document_id: form.value.document_id.trim() }
      : {}),
    category: form.value.category,
    material_type: form.value.material_type,
    is_demo: form.value.material_type === "synthetic",
    visibility: form.value.visibility,
    effective_from: new Date(form.value.effective_from).toISOString(),
    change_summary: form.value.change_summary,
    original_source: {
      title: form.value.source_title,
      url: form.value.source_url || null,
      edition: form.value.edition,
      publisher: form.value.publisher,
      acquired_on: form.value.acquired_on,
      note: form.value.note,
    },
    indexing_policy: {
      ...pages,
      legal_temporal_reviewed: form.value.legal_temporal_reviewed,
    },
  };
  if (!reuse || !uploadKey.value) uploadKey.value = newKey();
  saving.value = true;
  try {
    await api.upload(file.value, metadata, uploadKey.value);
    showUpload.value = false;
    file.value = null;
    success.value = "문서를 초안으로 등록했습니다. 검수 후 승인해 주세요";
    uploadKey.value = null;
    await load();
  } catch (e) {
    error.value = e;
    retry.value = () => upload(true);
  } finally {
    saving.value = false;
  }
}
function poll(jobId, version) {
  if (disposed) return;
  const timer = setTimeout(async () => {
    timers.delete(timer);
    try {
      const response = await api.job(jobId);
      const result = response.data;
      jobs.value[version.version_id] = result.status;
      if (result.status === "succeeded" || result.status === "failed") {
        await load(meta.value?.page || 1);
        if (selected.value)
          await openHistory(selected.value, historyPage.value);
        if (result.status === "failed")
          error.value = {
            ...api.indexJobFailure(result),
            requestId: response.request_id,
          };
        return;
      }
      poll(jobId, version);
    } catch (e) {
      error.value = e;
      retry.value = () => poll(jobId, version);
    }
  }, 2000);
  timers.add(timer);
}
async function act(operation) {
  const { version, action, key, review_note, notice_title } = operation;
  if (saving.value) return;
  saving.value = true;
  error.value = null;
  try {
    if (action === "approve") {
      const result = (
        await api.approve(
          version.document_id,
          version.version_id,
          review_note,
          key,
        )
      ).data;
      jobs.value[version.version_id] = result.index_status;
      poll(result.job_id, version);
      success.value = "승인됐습니다. 검색 데이터를 준비하고 있습니다.";
    } else {
      await api.publish(
        version.document_id,
        version.version_id,
        notice_title,
        key,
      );
      success.value =
        "배포와 변경 공지 생성이 완료됐습니다. 실제 적용 시점은 문서 적용일을 따릅니다.";
    }
    actionRequest.value = null;
    await load(meta.value?.page || 1);
    if (selected.value) await openHistory(selected.value, historyPage.value);
  } catch (e) {
    error.value = e;
    retry.value = () => act(operation);
  } finally {
    saving.value = false;
  }
}
onMounted(() => load());
onUnmounted(() => {
  disposed = true;
  loadGeneration++;
  historyGeneration++;
  for (const timer of timers) clearTimeout(timer);
});
</script>
<template>
  <section>
    <div class="page-heading">
      <div>
        <span class="eyebrow">DOCUMENT LIBRARY</span>
        <h1>
          {{ management ? "운영 기준을 관리하는 곳" : "필요한 기준이 모인 곳" }}
        </h1>
        <p>
          {{
            management
              ? "문서의 검수부터 배포까지, 판본별로 안전하게 관리하세요"
              : "접근이 허용된 문서와 적용 기준을 확인하세요"
          }}
        </p>
      </div>
      <button v-if="management" class="button" @click="startUpload">
        <Plus :size="17" />문서 등록
      </button>
    </div>
    <div class="info-banner">
      <FolderOpen :size="17" /><span>{{
        management
          ? "등록 → 검수·승인 → 검색 준비 → 배포 순서로 진행됩니다."
          : "문서의 버전과 적용일을 확인하고 사용해 주세요"
      }}</span>
    </div>
    <p v-if="success" class="success-notice" role="status">{{ success }}</p>
    <ErrorNotice :error="error" @retry="retry?.()" />
    <div class="surface">
      <div class="surface-toolbar">
        <h2>문서 목록</h2>
        <label class="inline-filter"
          >분류<select v-model="category" @change="load()">
            <option value="">전체 분류</option>
            <option v-for="(label, key) in categories" :key="key" :value="key">
              {{ label }}
            </option>
          </select></label
        >
      </div>
      <p v-if="busy" class="loading-line" role="status">문서를 불러오는 중…</p>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>문서명</th>
              <th>버전</th>
              <th>적용일</th>
              <th>배포 상태</th>
              <th>검색 준비</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.document_id">
              <td>
                <div class="document-title">
                  <span class="file-icon"><FileText :size="20" /></span>
                  <div>
                    <strong>{{ row.title }}</strong
                    ><small
                      >{{ categories[row.category] }} ·
                      {{
                        row.is_demo
                          ? "합성·시연 자료"
                          : row.material_type === "official"
                            ? "법령·고시"
                            : "정보공개서 원본"
                      }}</small
                    >
                  </div>
                </div>
              </td>
              <td>v{{ row.selected_version }}</td>
              <td>
                {{
                  row.effective_from
                    ? new Date(row.effective_from).toLocaleDateString("ko-KR", {
                        timeZone: "Asia/Seoul",
                      })
                    : "—"
                }}
              </td>
              <td>
                <StatusBadge :status="row.lifecycle" /><StatusBadge
                  :status="application(row)"
                />
              </td>
              <td>
                <StatusBadge :status="row.index_status" />
              </td>
              <td>
                <button class="button small ghost" @click="openHistory(row)">
                  판본 보기<ArrowUpRight :size="14" />
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="!busy && !rows.length" class="empty-state">
        <FolderOpen :size="30" />
        <h3>표시할 문서가 없습니다.</h3>
        <p>접근 범위와 선택한 분류를 확인해 주세요</p>
      </div>
      <PageNav :meta="meta" :busy="busy" @change="load" />
    </div>
    <div v-if="management" class="info-banner">
      배포 완료와 적용 시작은 다릅니다. 현재 적용 판본은 서버의 현재 시점 검색
      결과를 기준으로 표시합니다.
    </div>
    <div
      v-if="selected"
      class="modal-backdrop history-backdrop"
      @click.self="closeHistory"
    >
      <section
        class="surface history-surface history-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="history-title"
        v-focus-trap="closeHistory"
      >
        <div class="surface-toolbar">
          <div>
            <span class="eyebrow">VERSION HISTORY</span>
            <h2 id="history-title">{{ selected.title }} · 판본 이력</h2>
          </div>
          <button
            class="icon-button"
            aria-label="판본 이력 닫기"
            :disabled="saving"
            @click="closeHistory"
          >
            ✕
          </button>
        </div>
        <p v-if="historyBusy" class="history-loading" role="status">
          판본 목록을 불러오는 중…
        </p>
        <ErrorNotice
          :error="historyError"
          @retry="openHistory(selected, historyPage)"
        />
        <p
          v-if="!historyBusy && !historyError && !history.length"
          class="empty-state"
        >
          등록된 판본이 없습니다.
        </p>
        <article
          v-for="version in history"
          :key="version.version_id"
          class="version-row"
        >
          <div>
            <strong>v{{ version.version }}</strong
            ><StatusBadge :status="version.lifecycle" /><StatusBadge
              :status="version.index_status"
            />
            <StatusBadge :status="application(version)" />
            <p>{{ version.change_summary }}</p>
            <p v-if="version.material_type === 'official'" class="muted">
              {{
                version.indexing_policy.legal_temporal_reviewed
                  ? "법령 시행 시점 검수 기록 있음"
                  : "법령 시행 시점 미검수"
              }}
            </p>
            <small class="muted"
              >적용
              {{
                new Date(version.effective_from).toLocaleString("ko-KR", {
                  timeZone: "Asia/Seoul",
                })
              }}</small
            >
          </div>
          <div class="version-actions">
            <button
              class="button small secondary"
              @click="
                source = {
                  document_id: version.document_id,
                  version_id: version.version_id,
                  title: version.title,
                }
              "
            >
              원문·출처 확인</button
            ><button
              v-if="
                auth.can('document:approve') && version.lifecycle === 'draft'
              "
              class="button small"
              :disabled="saving"
              @click="beginAction(version, 'approve')"
            >
              검수 완료 · 승인</button
            ><button
              v-if="
                auth.can('document:publish') && version.lifecycle === 'approved'
              "
              class="button small"
              :disabled="saving || version.index_status !== 'ready'"
              @click="beginAction(version, 'publish')"
            >
              배포 · 공지 생성
            </button>
          </div>
        </article>
        <PageNav
          :meta="historyMeta"
          :busy="saving || historyBusy"
          @change="(page) => openHistory(selected, page)"
        />
      </section>
    </div>
    <div
      v-if="showUpload"
      class="modal-backdrop"
      @click.self="!saving && (showUpload = false)"
    >
      <section
        class="upload-dialog"
        v-focus-trap="() => !saving && (showUpload = false)"
        role="dialog"
        aria-modal="true"
        aria-labelledby="upload-title"
      >
        <div class="panel-heading">
          <span class="eyebrow">NEW DOCUMENT VERSION</span
          ><button
            class="icon-button"
            aria-label="문서 등록 닫기"
            :disabled="saving"
            @click="showUpload = false"
          >
            ✕
          </button>
        </div>
        <h2 id="upload-title">새 문서 또는 판본 등록</h2>
        <p class="muted">등록된 문서는 검수·승인 후 배포할 수 있습니다.</p>
        <div v-if="isMock" class="demo-upload-help">
          <button
            type="button"
            class="button small secondary"
            :disabled="saving"
            @click="fillRecipeDemo"
          >
            레시피 v1.1 등록 예시 채우기
          </button>
          <p class="muted">
            예시를 채운 뒤 data/staged/합성_카페_레시피_v1.1.pdf를 직접
            선택하세요<br />적용일은 2026년 10월 26일이며, 파일을 선택해도
            승인·배포되지는 않습니다.
          </p>
        </div>
        <ErrorNotice :error="error" @retry="retry?.()" />
        <form
          class="upload-form"
          :key="uploadFormKey"
          @submit.prevent="upload()"
          @input="uploadKey = null"
        >
          <label class="file-drop"
            ><UploadCloud :size="25" /><strong>{{
              file?.name || "PDF · TXT · Markdown 선택"
            }}</strong
            ><small>파일당 최대 10MB</small
            ><input
              type="file"
              accept=".pdf,.txt,.md,.markdown"
              required
              @change="chooseFile"
          /></label>
          <div class="form-grid">
            <label
              >문서 제목<input
                v-model="form.title"
                required
                maxlength="200" /></label
            ><label
              >버전<input
                v-model="form.version"
                required
                maxlength="50"
                placeholder="예: 1.2" /></label
            ><label
              >기존 문서 ID <small>새 문서면 비워두세요</small
              ><input
                v-model="form.document_id"
                maxlength="100"
                pattern="[A-Za-z0-9][A-Za-z0-9._-]*" /></label
            ><label
              >분류<select v-model="form.category">
                <option
                  v-for="(label, key) in categories"
                  :value="key"
                  :key="key"
                >
                  {{ label }}
                </option>
              </select></label
            ><label
              >자료 유형<select v-model="form.material_type">
                <option value="synthetic">합성·시연 자료</option>
                <option value="official">법령·고시</option>
                <option value="public_disclosure">정보공개서 원본</option>
              </select></label
            ><label
              >최소 열람 역할<select v-model="form.visibility">
                <option value="prospect">예비 점주 이상</option>
                <option value="owner">운영 점주 이상</option>
                <option value="hq">본사 전용</option>
              </select></label
            ><label
              >적용 시각<input
                v-model="form.effective_from"
                type="datetime-local"
                required /></label
            ><label
              >변경 요약<input
                v-model="form.change_summary"
                required
                maxlength="2000"
            /></label>
          </div>
          <h3>원천 출처</h3>
          <div class="form-grid">
            <label
              >출처 제목<input
                v-model="form.source_title"
                required
                maxlength="200" /></label
            ><label
              >발행 주체<input
                v-model="form.publisher"
                required
                maxlength="100" /></label
            ><label
              >출처 판본<input
                v-model="form.edition"
                required
                maxlength="100" /></label
            ><label
              >취득일<input
                v-model="form.acquired_on"
                type="date"
                required /></label
            ><label
              >출처 URL <small>없으면 생략</small
              ><input v-model="form.source_url" type="url" /></label
            ><label
              >출처 메모<input
                v-model="form.note"
                maxlength="2000"
                placeholder="합성 여부·이용 범위 등" /></label
            ><label
              >검색에 포함할 페이지
              <small>비우면 전체에서 제외 쪽만 뺍니다</small
              ><input
                v-model="form.include_pages"
                placeholder="예: 3, 4, 5" /></label
            ><label
              >검색에서 제외할 페이지<input
                v-model="form.exclude_pages"
                placeholder="예: 1, 2" /></label
            ><label class="checkbox-label"
              ><input
                v-model="form.legal_temporal_reviewed"
                type="checkbox"
              />법령 시행 시점을 검수했습니다</label
            >
          </div>
          <p class="muted">
            쪽수는 PDF 파일 기준입니다. 실제 전체 쪽수와 색인 가능한 본문 유무는
            서버에서 확인합니다.
          </p>
          <button class="button" :disabled="saving">
            {{ saving ? "등록 중…" : "초안으로 등록" }}
          </button>
        </form>
      </section>
    </div>
    <div
      v-if="actionRequest"
      class="modal-backdrop"
      @click.self="!saving && (actionRequest = null)"
    >
      <section
        class="source-panel"
        v-focus-trap="() => !saving && (actionRequest = null)"
        role="dialog"
        aria-modal="true"
        aria-labelledby="action-title"
      >
        <div class="panel-heading">
          <span class="eyebrow">DOCUMENT REVIEW</span
          ><button
            class="icon-button"
            :disabled="saving"
            aria-label="검토 닫기"
            @click="actionRequest = null"
          >
            ✕
          </button>
        </div>
        <h2 id="action-title">
          {{
            actionRequest.action === "approve"
              ? "검수 내용 확인 후 승인"
              : "변경 공지 확인 후 배포"
          }}
        </h2>
        <h3>
          {{ actionRequest.version.title }} · v{{
            actionRequest.version.version
          }}
        </h3>
        <p>{{ actionRequest.version.change_summary }}</p>
        <p>
          적용 시각 ·
          {{
            new Date(actionRequest.version.effective_from).toLocaleString(
              "ko-KR",
              { timeZone: "Asia/Seoul" },
            )
          }}
        </p>
        <p>
          열람 대상 ·
          {{
            {
              owner: "운영 점주·본사",
              prospect: "예비 점주·운영 점주·본사",
              hq: "본사 전용",
            }[actionRequest.version.visibility]
          }}
        </p>
        <p class="info-banner">
          적용일이 미래이면 배포 후에도 해당 날짜 전까지 현재 기준을 대체하지
          않습니다.
        </p>
        <ErrorNotice :error="error" @retry="retry?.()" />
        <form @submit.prevent="submitAction">
          <fieldset :disabled="saving">
            <template v-if="actionRequest.action === 'approve'"
              ><label
                v-for="(label, i) in checkLabels"
                :key="label"
                class="checkbox-label"
                ><input v-model="checks[i]" type="checkbox" required />{{
                  label
                }}</label
              ><label
                >검수 메모<textarea
                  v-model="reviewNote"
                  required
                  maxlength="2000"
                  rows="4"
                  placeholder="무엇을 확인했는지 기록하세요"
                ></textarea></label></template
            ><label v-else
              >점포에 표시할 공지 제목<input
                v-model="noticeTitle"
                required
                maxlength="200" /></label
            ><button
              class="button"
              :disabled="
                saving ||
                (actionRequest.action === 'approve' && !checks.every(Boolean))
              "
            >
              {{
                saving
                  ? "처리 중…"
                  : actionRequest.action === "approve"
                    ? "검수 내용으로 승인"
                    : "배포하고 변경 공지 생성"
              }}
            </button>
          </fieldset>
        </form>
      </section>
    </div>
    <SourcePanel v-if="source" :source="source" @close="source = null" />
  </section>
</template>
