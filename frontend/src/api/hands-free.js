import { browserListen, speakLocally, normalizeCommand } from "./voice";
import { spokenAnswer } from "./spoken-answer";
import { normalizeWakeAlias, validWakeAlias } from "./wake-profile";

export function wakeQuestion(text, aliases = []) {
  const match = text
    .trim()
    .match(
      /^(?:헤이\s*)?(?:시\s*리|siri)(?:\s*야)?(?=[\s,.!?。]|$)[\s,.!?。]*/i,
    );
  let prefix = match?.[0] || "";
  for (const alias of aliases.filter(validWakeAlias).map(normalizeWakeAlias)) {
    const pattern = new RegExp(
      `^(?:헤이\\s*)?${[...alias].join("\\s*")}(?:\\s*야)?[\\s,.!?。]*`,
    );
    const personal = text.trim().match(pattern)?.[0];
    if (personal && personal.length > prefix.length) prefix = personal;
  }
  return prefix ? text.trim().slice(prefix.length).trim() : null;
}
export const cancelQuestion = (text) =>
  /^(취소|그만|다시|질문취소)$/.test(normalizeCommand(text));
const aborted = () =>
  new DOMException("음성 대기를 종료했습니다.", "AbortError");
function pause(signal) {
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(aborted());
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, 350);
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
  });
}

// Playback and a single wake-only recognizer run together. A new call stops
// playback before its question is handled; all children stop with the session.
export async function speakWithWakeInterrupt(
  text,
  { signal, listen, speak, getAliases = () => [] },
) {
  const playbackController = new AbortController();
  const monitorController = new AbortController();
  const abort = () => {
    playbackController.abort();
    monitorController.abort();
  };
  signal.addEventListener("abort", abort, { once: true });
  if (signal.aborted) abort();
  const played = Promise.resolve()
    .then(() => speak(text, { signal: playbackController.signal }))
    .then((ok) => ({ kind: "played", ok }));
  const monitored = (async () => {
    try {
      while (!monitorController.signal.aborted) {
        const heard = await listen({
          signal: monitorController.signal,
          maxSeconds: 30,
          allowEmpty: true,
          stopOnFinal: (value) => wakeQuestion(value, getAliases()) !== null,
        });
        if (monitorController.signal.aborted) throw aborted();
        if (wakeQuestion(heard, getAliases()) !== null)
          return { kind: "wake", heard };
        await pause(monitorController.signal);
      }
    } catch {
      return { kind: "monitor-unavailable" };
    }
    return { kind: "monitor-unavailable" };
  })();
  try {
    let result = await Promise.race([played, monitored]);
    if (result.kind === "monitor-unavailable")
      result = { ...(await played), monitorUnavailable: !signal.aborted };
    if (result.kind === "wake") {
      playbackController.abort();
      return { heard: result.heard, ok: true };
    }
    return {
      heard: null,
      ok: result.ok,
      monitorUnavailable: result.monitorUnavailable,
    };
  } finally {
    abort();
    signal.removeEventListener("abort", abort);
  }
}

// One recognizer at a time, including the wake monitor during playback.
export function createHandsFree({
  ask,
  allowReadAloud = true,
  onState = () => {},
  onPartial = () => {},
  onHeard = () => {},
  onError = () => {},
  onNotice = () => {},
  getAliases = () => [],
  listen = browserListen,
  speak = speakLocally,
}) {
  let controller = null;
  function stop() {
    controller?.abort();
    controller = null;
    onPartial("");
    onState("off");
  }
  async function start() {
    stop();
    const session = new AbortController();
    controller = session;
    const { signal } = session;
    const check = () => {
      if (signal.aborted) throw aborted();
    };
    let queuedWake = null;
    try {
      while (!signal.aborted) {
        onPartial("");
        onState("waiting");
        const heard =
          queuedWake ??
          (await listen({
            signal,
            maxSeconds: 30,
            allowEmpty: true,
            onPartial: (text) => {
              if (!signal.aborted) onHeard(text);
            },
            stopOnFinal: (text) => wakeQuestion(text, getAliases()) !== null,
          }));
        queuedWake = null;
        check();
        if (heard) onHeard(heard);
        let question = wakeQuestion(heard, getAliases());
        if (question === null) {
          await pause(signal);
          continue;
        }
        if (!question) {
          onState("prompting");
          await speak("네, 말씀하세요", { signal });
          check();
          onState("question");
          question = (
            await listen({
              signal,
              maxSeconds: 60,
              allowEmpty: true,
              onPartial,
            })
          ).trim();
          check();
        }
        if (!question || cancelQuestion(question)) {
          await pause(signal);
          continue;
        }
        if (question.length > 2000)
          throw new Error(
            "질문이 너무 깁니다. 음성 대기를 다시 켜고 짧게 질문해 주세요",
          );
        onPartial(question);
        onState("thinking");
        const result = await ask(question);
        check();
        if (!result)
          throw new Error(
            "답변을 가져오지 못해 음성 대기를 껐습니다. 화면의 오류를 확인해 주세요",
          );
        if (allowReadAloud && result.answer) {
          onState("speaking");
          const playback = await speakWithWakeInterrupt(
            spokenAnswer(result, question),
            { signal, listen, speak, getAliases },
          );
          if (playback.heard !== null) queuedWake = playback.heard;
          check();
          if (playback.monitorUnavailable)
            onNotice(
              "이 브라우저에서 읽는 중 호출어 인식이 중단됐어요. 이번 답변은 대기 끄기 버튼으로 멈출 수 있어요",
            );
          if (!playback.ok)
            onNotice(
              "기기에 한국어 읽기 음성이 없어 답변은 화면으로 표시합니다.",
            );
        }
        if (queuedWake === null) await pause(signal);
      }
    } catch (error) {
      if (!signal.aborted)
        onError(error.message || "음성 대기를 다시 켜 주세요");
    } finally {
      // An old request finishing must not turn off a newly started session.
      if (controller === session) stop();
    }
  }
  return { start, stop };
}
