import { request } from "./client";
export const summary = (params) =>
  request({ url: "/analytics/questions", params });
export const events = (params) =>
  request({ url: "/analytics/question-events", params });
export const event = (id) =>
  request({ url: `/analytics/question-events/${encodeURIComponent(id)}` });
export const update = (id, data) =>
  request({
    method: "patch",
    url: `/analytics/question-events/${encodeURIComponent(id)}`,
    data,
  });
export const options = (kind, params) => request({ url: `/${kind}`, params });
