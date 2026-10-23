import { request } from "./client";
export const list = (params) => request({ url: "/notices", params });
export const ack = (id) =>
  request({
    method: "put",
    url: `/notices/${encodeURIComponent(id)}/ack`,
    data: {},
  });
export const acknowledgements = (id, params) =>
  request({
    url: `/notices/${encodeURIComponent(id)}/acknowledgements`,
    params,
  });

// Existing API exposes per-notice summaries. Filter the full list before paging.
export async function listOutstanding({ page = 1, page_size = 10 } = {}) {
  const notices = [];
  for (let current = 1; ; current++) {
    const response = await list({ page: current, page_size: 100 });
    notices.push(...response.data);
    if (current >= response.meta.total_pages) break;
  }
  const outstanding = [];
  for (let offset = 0; offset < notices.length; offset += 5) {
    const batch = notices.slice(offset, offset + 5);
    const results = await Promise.all(
      batch.map((row) =>
        acknowledgements(row.notice_id, { page: 1, page_size: 1 }),
      ),
    );
    results.forEach((response, index) => {
      const count = response.summary?.unacknowledged_count;
      if (!Number.isInteger(count) || count < 0)
        throw new Error(
          "점포 확인 현황을 확인하지 못했습니다. 다시 시도해 주세요.",
        );
      if (count > 0) outstanding.push(batch[index]);
    });
  }
  const total = outstanding.length;
  return {
    data: outstanding.slice((page - 1) * page_size, page * page_size),
    meta: { page, page_size, total, total_pages: Math.ceil(total / page_size) },
  };
}
