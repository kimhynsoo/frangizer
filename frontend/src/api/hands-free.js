import { browserListen, speakLocally, normalizeCommand } from "./voice";
import { spokenAnswer } from "./spoken-answer";
import {
  followUpPresentation,
  hasFollowUpQuestion,
  temperatureChoice,
} from "./follow-up";
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
  let controller = null,
    retryRequest = null;
  function retry() {
    retryRequest?.();
  }
  function waitForRetry(signal) {
    return new Promise((resolve, reject) => {
      const cleanup = () => {
        signal.removeEventListener("abort", abort);
        retryRequest = null;
      };
      const abort = () => {
        cleanup();
        reject(aborted());
      };
      retryRequest = () => {
        cleanup();
        resolve();
      };
      signal.addEventListener("abort", abort, { once: true });
      if (signal.aborted) abort();
    });
  }
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
        try {
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
            await speak("네", { signal });
            check();
            onState("question");
            question = (
              await listen({
                signal,
                maxSeconds: 60,
                allowEmpty: true,
                onPartial,
                // Browser speech recognition can keep the session open for a
                // few seconds after it already produced the final sentence.
                // The question is complete at that point, so submit it now.
                stopOnFinal: () => true,
              })
            ).trim();
            check();
          }
          if (!question || cancelQuestion(question)) {
            await pause(signal);
            continue;
          }
          if (question.length > 2000) {
            onNotice(
              "질문이 너무 깁니다. 호출어를 부르고 짧게 다시 질문해 주세요",
            );
            await pause(signal);
            continue;
          }
          onPartial(question);
          onState("thinking");
          let result;
          try {
            result = await ask(question);
          } catch {
            /* The chat view keeps the failed request and its retry control. */
          }
          check();
          if (!result) {
            onNotice(
              "답변을 가져오지 못했어요. 음성 대기는 유지합니다. 호출어를 부르고 다시 질문해 주세요",
            );
            await pause(signal);
            continue;
          }
          const expectsFollowUp =
            allowReadAloud && hasFollowUpQuestion(result);
          const expectsTemperature =
            expectsFollowUp && followUpPresentation(result).temperature;
          if (allowReadAloud && result.answer) {
            onState("speaking");
            let playback;
            try {
              playback = expectsFollowUp
                ? {
                    heard: null,
                    ok: await speak(spokenAnswer(result, question), { signal }),
                  }
                : await speakWithWakeInterrupt(spokenAnswer(result, question), {
                    signal,
                    listen,
                    speak,
                    getAliases,
                  });
            } catch {
              check();
              onNotice(
                "답변을 읽지 못했지만 음성 대기는 유지합니다. 화면의 답변을 확인해 주세요",
              );
              await pause(signal);
              continue;
            }
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
          if (queuedWake === null && expectsFollowUp) {
            onPartial("");
            onState("question");
            const spokenChoice = (
              await listen({
                signal,
                maxSeconds: 15,
                allowEmpty: true,
                onPartial,
                stopOnFinal: () => true,
              })
            ).trim();
            check();
            if (cancelQuestion(spokenChoice)) {
              await pause(signal);
              continue;
            }
            const choice = expectsTemperature
              ? temperatureChoice(spokenChoice)
              : spokenChoice;
            if (!choice) {
              onNotice(
                expectsTemperature
                  ? "온도를 확인하지 못했어요. 핫 또는 아이스라고 바로 말씀해 주세요"
                  : "후속 답변을 듣지 못했어요. 다시 호출한 뒤 질문해 주세요",
              );
              await pause(signal);
              continue;
            }
            onPartial(choice);
            onState("thinking");
            let followUpResult;
            try {
              followUpResult = await ask(choice);
            } catch {
              /* The chat view keeps the failed request and its retry control. */
            }
            check();
            if (!followUpResult) {
              onNotice(
                "후속 답변을 가져오지 못했어요. 음성 대기는 유지합니다. 호출어를 부르고 다시 질문해 주세요",
              );
              await pause(signal);
              continue;
            }
            if (followUpResult.answer) {
              onState("speaking");
              const playback = await speakWithWakeInterrupt(
                spokenAnswer(followUpResult, choice),
                { signal, listen, speak, getAliases },
              );
              if (playback.heard !== null) queuedWake = playback.heard;
              check();
              if (!playback.ok)
                onNotice(
                  "기기에 한국어 읽기 음성이 없어 답변은 화면으로 표시합니다.",
                );
            }
          }
          if (queuedWake === null) await pause(signal);
        } catch (error) {
          if (signal.aborted) break;
          onError(error.message || "음성 인식 연결을 확인해 주세요");
          onState("paused");
          await waitForRetry(signal);
          check();
        }
      }
    } catch (error) {
      if (!signal.aborted)
        onError(error.message || "음성 대기를 다시 켜 주세요");
    } finally {
      // An old request finishing must not turn off a newly started session.
      if (controller === session) stop();
    }
  }
  return { start, stop, retry };
}
