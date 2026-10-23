<script setup>
import { ref, onMounted } from "vue";
import { Bell, Check, ArrowUpRight } from "@lucide/vue";
import { useAuth } from "../stores/auth";
import { useNotices } from "../stores/notices";
import * as api from "../api/notices";
import { options } from "../api/analytics";
import SourcePanel from "../components/SourcePanel.vue";
import PageNav from "../components/PageNav.vue";
import ErrorNotice from "../components/ErrorNotice.vue";
const auth = useAuth(),
  noticeStore = useNotices(),
  rows = ref([]),
  meta = ref(null),
  unread = ref(false),
  busy = ref(false),
  error = ref(null),
  source = ref(null),
  selected = ref(null),
  targets = ref([]),
  summary = ref(null),
  targetMeta = ref(null),
  filter = ref(""),
  region = ref(""),
  regions = ref([]),
  ackBusy = ref(""),
  retry = ref(null);
let generation = 0;
async function load(page = 1) {
  const current = ++generation;
  busy.value = true;
  error.value = null;
  try {
    const r = await api.list({
      page,
      page_size: 10,
      ...(unread.value ? { unread: true } : {}),
    });
    if (current === generation) {
      rows.value = r.data;
      meta.value = r.meta;
    }
  } catch (e) {
    error.value = e;
    retry.value = () => load(page);
  } finally {
    if (current === generation) busy.value = false;
  }
}
async function ack(row) {
  ackBusy.value = row.notice_id;
  error.value = null;
  try {
    await api.ack(row.notice_id);
    await load(meta.value.page);
    await noticeStore.refresh();
  } catch (e) {
    error.value = e;
    retry.value = () => ack(row);
  } finally {
    ackBusy.value = "";
  }
}
async function showTargets(row, page = 1) {
  selected.value = row;
  error.value = null;
  try {
    const r = await api.acknowledgements(row.notice_id, {
      page,
      page_size: 10,
      ...(filter.value ? { acknowledged: filter.value } : {}),
      ...(region.value ? { region_code: region.value } : {}),
    });
    targets.value = r.data;
    summary.value = r.summary;
    targetMeta.value = r.meta;
  } catch (e) {
    error.value = e;
    retry.value = () => showTargets(row, page);
  }
}
onMounted(async () => {
  await load();
  if (auth.can("analytics:read")) {
    try {
      let page = 1;
      while (true) {
        const response = await options("regions", { page, page_size: 100 });
        regions.value.push(...response.data);
        if (page >= response.meta.total_pages) break;
        page++;
      }
    } catch (e) {
      error.value = e;
    }
  }
});
</script>
<template>
  <section>
    <div class="page-heading">
      <div>
        <span class="eyebrow">UPDATES & NOTICES</span>
        <h1>바뀐 기준, 놓치지 않도록.</h1>
        <p>운영 문서의 변경 내용과 적용일을 확인하세요.</p>
      </div>
      <Bell :size="29" class="heading-icon" />
    </div>
    <ErrorNotice :error="error" @retry="retry?.()" />
    <div class="surface-toolbar notice-toolbar">
      <h2>
        변경 공지 <span class="muted">{{ meta?.total || 0 }}</span>
      </h2>
      <label class="checkbox-label"
        ><input v-model="unread" type="checkbox" @change="load()" />미확인만
        보기</label
      >
    </div>
    <p v-if="busy" role="status" class="loading-line">공지를 불러오는 중…</p>
    <article v-for="row in rows" :key="row.notice_id" class="notice-card">
      <div class="notice-card-top">
        <div>
          <span v-if="!row.acknowledged_at" class="badge needs_review">{{
            auth.user.role === "owner" ? "미확인" : "변경 안내"
          }}</span
          ><span v-else class="badge answered"
            ><Check :size="12" />확인 완료</span
          ><small
            >{{
              new Date(row.published_at).toLocaleDateString("ko-KR", {
                timeZone: "Asia/Seoul",
              })
            }}
            배포</small
          >
        </div>
        <span class="muted">문서 변경</span>
      </div>
      <h2>{{ row.title }}</h2>
      <p>{{ row.change_summary }}</p>
      <span
        v-if="new Date(row.effective_from) > new Date()"
        class="badge scheduled"
        >적용 예정 · 현재 기준은 유지</span
      >
      <div class="notice-effective">
        적용 시점
        <strong>{{
          new Date(row.effective_from).toLocaleString("ko-KR", {
            timeZone: "Asia/Seoul",
          })
        }}</strong>
      </div>
      <div class="notice-actions">
        <button
          class="button small ghost"
          @click="
            source = {
              document_id: row.document_id,
              version_id: row.version_id,
              title: row.title,
            }
          "
        >
          문서 확인<ArrowUpRight :size="14" /></button
        ><button
          v-if="auth.user.role === 'owner'"
          class="button small"
          :disabled="!!row.acknowledged_at || ackBusy === row.notice_id"
          @click="ack(row)"
        >
          {{
            row.acknowledged_at
              ? "확인 완료"
              : ackBusy === row.notice_id
                ? "저장 중…"
                : "공지 확인했어요"
          }}</button
        ><button
          v-if="auth.can('analytics:read')"
          class="button small secondary"
          @click="showTargets(row)"
        >
          점포 확인 현황
        </button>
      </div>
    </article>
    <div v-if="!busy && !rows.length" class="empty-card">
      <Bell :size="30" />
      <h2>
        {{ unread ? "모든 공지를 확인했어요." : "등록된 공지가 없습니다." }}
      </h2>
      <p>새로운 변경사항이 생기면 이곳에서 확인할 수 있어요.</p>
    </div>
    <PageNav :meta="meta" :busy="busy" @change="load" />
    <p class="composer-note">
      공지 확인 기록은 내용을 확인했다는 의미이며, 실제 현장 적용 완료를 뜻하지
      않습니다.
    </p>
    <div v-if="selected && summary" class="surface history-surface">
      <div class="surface-toolbar">
        <div>
          <span class="eyebrow">ACKNOWLEDGEMENTS</span>
          <h2>{{ selected.title }}</h2>
        </div>
        <button
          class="icon-button"
          aria-label="확인 현황 닫기"
          @click="selected = null"
        >
          ✕
        </button>
      </div>
      <div class="ack-summary">
        <strong
          >{{ summary.acknowledged_count
          }}<small> / {{ summary.target_count }}점포 확인</small></strong
        ><span
          >확인율
          {{
            summary.acknowledgement_rate === null
              ? "N/A"
              : `${Math.round(summary.acknowledgement_rate * 100)}%`
          }}</span
        >
      </div>
      <div class="surface-toolbar">
        <p class="muted">전체 대상 기준 · 확인 여부·지역 필터와 무관한 집계</p>
        <label class="inline-filter"
          >지역<select v-model="region" @change="showTargets(selected)">
            <option value="">전체 지역</option>
            <option v-for="item in regions" :key="item.code" :value="item.code">
              {{ item.name }}
            </option>
          </select></label
        >
        <label class="inline-filter"
          >확인 여부<select v-model="filter" @change="showTargets(selected)">
            <option value="">전체</option>
            <option value="true">확인</option>
            <option value="false">미확인</option>
          </select></label
        >
      </div>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>점포</th>
              <th>지역 코드</th>
              <th>확인 시각</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in targets" :key="item.store_id">
              <td>{{ item.store_name }}</td>
              <td>
                {{
                  regions.find((region) => region.code === item.region_code)
                    ?.name || item.region_code
                }}
              </td>
              <td>
                {{
                  item.acknowledged_at
                    ? new Date(item.acknowledged_at).toLocaleString("ko-KR", {
                        timeZone: "Asia/Seoul",
                      })
                    : "미확인"
                }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <PageNav
        :meta="targetMeta"
        @change="(page) => showTargets(selected, page)"
      />
    </div>
  </section>
  <SourcePanel v-if="source" :source="source" @close="source = null" />
</template>
