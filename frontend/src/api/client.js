import axios from "axios";

export const isMock = import.meta.env.VITE_USE_MOCK !== "false";
const configuredBase = import.meta.env.VITE_API_BASE_URL?.trim();
export const baseURL =
  configuredBase ||
  (import.meta.env.DEV ? "http://localhost:8080/api/v1" : "/api/v1");
const tokenKey = `frangizer.${isMock ? "demo" : "live"}.token`;
export const tokenStorage = {
  get: () => sessionStorage.getItem(tokenKey),
  set: (value) => sessionStorage.setItem(tokenKey, value),
  clear: () => sessionStorage.removeItem(tokenKey),
};
export const newKey = () => crypto.randomUUID();
export const client = axios.create({ baseURL, timeout: 35000 });
client.interceptors.request.use((config) => {
  const token = tokenStorage.get();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});
client.interceptors.response.use(
  (response) => response,
  (error) => {
    const payload = error.response?.data;
    error.message =
      payload?.error?.message ||
      (error.code === "ECONNABORTED"
        ? "응답 시간이 초과됐습니다. 잠시 후 다시 시도해 주세요."
        : "서버에 연결할 수 없습니다. 연결 상태를 확인해 주세요.");
    error.details = payload?.error?.details || [];
    error.requestId = payload?.request_id;
    error.retryable = payload?.error?.retryable ?? !error.response;
    error.retryAfter = Number(error.response?.headers?.["retry-after"]) || null;
    if (
      error.response?.status === 401 &&
      !error.config?.url?.endsWith("/login")
    ) {
      tokenStorage.clear();
      window.dispatchEvent(new CustomEvent("session-expired"));
    }
    return Promise.reject(error);
  },
);
if (isMock) {
  const { mockAdapter } = await import("../demo/mock.js");
  client.defaults.adapter = mockAdapter;
}
export const request = async (config) => (await client.request(config)).data;
export const mutation = (url, data, key) =>
  request({ method: "post", url, data, headers: { "Idempotency-Key": key } });
// Authenticated download: do not navigate directly to a Bearer-protected file URL.
export async function downloadFile(path, name) {
  if (
    !/^\/api\/v1\/documents\/[A-Za-z0-9][A-Za-z0-9._-]*\/versions\/[A-Za-z0-9][A-Za-z0-9._-]*\/file$/.test(
      path,
    )
  ) {
    throw new Error("허용된 문서 다운로드 경로가 아닙니다.");
  }
  const result = await client.get(path.replace(/^\/api\/v1/, ""), {
    responseType: "blob",
  });
  const url = URL.createObjectURL(result.data);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download =
    isMock && result.data.type?.startsWith("text/plain") && /\.pdf$/i.test(name)
      ? "합성_시연_문서.txt"
      : name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
