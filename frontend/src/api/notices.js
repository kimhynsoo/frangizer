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
