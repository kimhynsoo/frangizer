import { request, isMock } from "./client";
export const MAX_AUDIO_BYTES = 10 * 1024 * 1024;
export async function transcribe(audio, { signal } = {}) {
  const type = audio.type.split(";")[0].toLowerCase();
  if (!["audio/webm", "audio/wav", "audio/x-wav", "audio/wave"].includes(type))
    throw new Error("WebM 또는 WAV 음성만 전송할 수 있습니다.");
  if (!audio.size || audio.size > MAX_AUDIO_BYTES)
    throw new Error("음성은 10MB 이하로 녹음해 주세요.");
  // Demo mode must never turn arbitrary recorded audio into a canned transcript.
  if (isMock)
    throw new Error(
      "샘플 모드에는 음성 서버가 없습니다. 브라우저 음성 체험을 선택하거나 실제 API에 연결해 주세요.",
    );
  const form = new FormData();
  form.append(
    "audio",
    audio,
    type === "audio/webm" ? "question.webm" : "question.wav",
  );
  const response = await request({
    method: "post",
    url: "/stt",
    data: form,
    signal,
  });
  const text = response.data?.text?.trim();
  if (!text)
    throw new Error("말소리를 인식하지 못했습니다. 메뉴명을 다시 말해 주세요.");
  if (text.length > 2000)
    throw new Error("인식 문장이 너무 깁니다. 질문을 짧게 다시 말해 주세요.");
  return { ...response.data, text };
}
