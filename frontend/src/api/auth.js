import { request } from "./client";
export const login = (credentials) =>
  request({ method: "post", url: "/login", data: credentials });
export const me = () => request({ url: "/me" });
