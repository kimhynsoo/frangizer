import { request, mutation } from "./client";
export const ask = (data, key) => mutation("/rag-query", data, key);
export const conversation = (id) =>
  request({ url: `/conversations/${encodeURIComponent(id)}` });
export const feedback = (id, helpful) =>
  request({
    method: "put",
    url: `/questions/${encodeURIComponent(id)}/feedback`,
    data: { helpful },
  });
export const conversationMessages = (messages) =>
  messages.map((message) => ({
    id: message.question_id,
    question: message.question_redacted,
    result: message.result,
    status: message.answer_status,
    errorCode: message.error_code,
  }));
