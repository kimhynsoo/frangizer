<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
import * as documents from "../api/documents";
import { newKey } from "../api/client";
import ErrorNotice from "./ErrorNotice.vue";
const emit = defineEmits(["close", "registered"]);
const rows = ref([]),
  versions = ref([]),
  documentId = ref(""),
  versionId = ref(""),
  title = ref(""),
  loading = ref(false),
  saving = ref(false),
  error = ref(null),
  retry = ref(null);
const selected = computed(() =>
  versions.value.find((v) => v.version_id === versionId.value),
);
let disposed = false,
  generation = 0,
  pending = null;
async function allPages(fetch) {
  const result = [];
  for (let page = 1; ; page++) {
    const response = await fetch({ page, page_size: 100 });
    if (disposed) return [];
    result.push(...response.data);
    if (page >= response.meta.total_pages) return result;
  }
}
async function loadDocuments() {
  loading.value = true;
  error.value = null;
  try {
    const result = await allPages((params) =>
      documents.list({ ...params, management: true }),
    );
    if (!disposed) rows.value = result;
  } catch (e) {
    if (!disposed) {
      error.value = e;
      retry.value = loadDocuments;
    }
  } finally {
    if (!disposed) loading.value = false;
  }
}
async function loadVersions() {
  const current = ++generation,
    id = documentId.value;
  versions.value = [];
  versionId.value = "";
  title.value = "";
  pending = null;
  error.value = null;
  if (!id) {
    loading.value = false;
    return;
  }
  loading.value = true;
  try {
    const result = await allPages((params) => documents.versions(id, params));
    if (!disposed && current === generation)
      versions.value = result.filter(
        (v) => v.lifecycle === "approved" && v.index_status === "ready",
      );
  } catch (e) {
    if (!disposed && current === generation) {
      error.value = e;
      retry.value = loadVersions;
    }
  } finally {
    if (!disposed && current === generation) loading.value = false;
  }
}
function chooseVersion() {
  title.value = selected.value
    ? `${selected.value.title} v${selected.value.version} 적용 안내`
    : "";
  pending = null;
}
async function publish() {
  const version = selected.value;
  if (!version || !title.value.trim() || saving.value || loading.value) return;
  const snapshot = {
    documentId: documentId.value,
    versionId: version.version_id,
    title: title.value.trim(),
  };
  if (!pending || pending.fingerprint !== JSON.stringify(snapshot))
    pending = {
      ...snapshot,
      fingerprint: JSON.stringify(snapshot),
      key: newKey(),
    };
  const attempt = pending;
  saving.value = true;
  error.value = null;
  try {
    const response = await documents.publish(
      attempt.documentId,
      attempt.versionId,
      attempt.title,
      attempt.key,
    );
    if (!disposed) emit("registered", response.data);
  } catch (e) {
    if (!disposed) {
      error.value = e;
      retry.value = publish;
    }
  } finally {
    if (!disposed) saving.value = false;
  }
}
function close() {
  if (!saving.value) emit("close");
}
onMounted(loadDocuments);
onUnmounted(() => {
  disposed = true;
  generation++;
});
</script>
<template>
  <div class="modal-backdrop acknowledgements-backdrop" @click.self="close">
    <section
      class="surface notice-registration-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="notice-registration-title"
      v-focus-trap="close"
      tabindex="-1"
    >
      <div class="surface-toolbar">
        <h2 id="notice-registration-title">공지 등록</h2>
        <button
          class="icon-button"
          aria-label="공지 등록 닫기"
          :disabled="saving"
          @click="close"
        >
          ✕
        </button>
      </div>
      <div class="notice-registration-body">
        <p class="info-banner">
          승인·검색 준비가 완료된 판본을 선택하세요. 공지를 등록하면 해당 판본이
          배포되고 대상 점포에 변경 공지가 생성됩니다.
        </p>
        <ErrorNotice :error="error" @retry="retry?.()" />
        <p v-if="loading" class="loading-line" role="status">
          문서와 판본을 불러오는 중…
        </p>
        <form @submit.prevent="publish">
          <fieldset :disabled="saving || loading">
            <label
              >변경 문서<select
                v-model="documentId"
                required
                @change="loadVersions"
              >
                <option value="">문서 선택</option>
                <option
                  v-for="row in rows"
                  :key="row.document_id"
                  :value="row.document_id"
                >
                  {{ row.title }}
                </option>
              </select></label
            >
            <label
              >배포할 판본<select
                v-model="versionId"
                required
                :disabled="!documentId || !versions.length || loading || saving"
                @change="chooseVersion"
              >
                <option value="">판본 선택</option>
                <option
                  v-for="version in versions"
                  :key="version.version_id"
                  :value="version.version_id"
                >
                  v{{ version.version }} · 승인·검색 준비 완료
                </option>
              </select></label
            >
            <p
              v-if="documentId && !loading && !versions.length && !error"
              class="muted"
            >
              배포 가능한 판본이 없습니다. 문서 관리에서 검수·승인과 검색 준비를
              완료하세요.
            </p>
            <label
              >공지 제목<input
                v-model="title"
                required
                maxlength="200"
                placeholder="점포에 표시할 공지 제목"
            /></label>
            <div v-if="selected" class="notice-publish-summary">
              <h3>변경 내용</h3>
              <p>{{ selected.change_summary }}</p>
              <p>
                적용 시점:
                {{
                  new Date(selected.effective_from).toLocaleString("ko-KR", {
                    timeZone: "Asia/Seoul",
                  })
                }}
              </p>
              <p>
                최소 열람 역할:
                {{
                  {
                    prospect: "예비 점주 이상",
                    owner: "운영 점주 이상",
                    hq: "본사 전용",
                  }[selected.visibility]
                }}
              </p>
              <p v-if="new Date(selected.effective_from) > new Date()">
                미래 적용 판본은 적용 시점 전까지 현재 기준을 대체하지 않습니다.
              </p>
            </div>
            <div class="notice-registration-actions">
              <button type="button" class="button secondary" @click="close">
                취소</button
              ><button
                class="button"
                :disabled="!selected || !title.trim() || loading || saving"
              >
                {{ saving ? "등록 중…" : "판본 배포 · 공지 등록" }}
              </button>
            </div>
          </fieldset>
        </form>
      </div>
    </section>
  </div>
</template>
