// Media hardware and browser speech are isolated here so lifecycle behavior is testable.
export const normalizeCommand = (text) =>
  text.trim().replace(/[\s.!?,。]/g, "");
export function voiceCommand(text) {
  const value = normalizeCommand(text);
  if (
    /^(보내|보내기|질문보내|질문보내기|확인|전송|전송해|전송하기)$/.test(value)
  )
    return "send";
  if (/^(취소|그만|중지|멈춰|종료)$/.test(value)) return "cancel";
  if (/^(다시|다시말할게|다시말하기|다시녹음)$/.test(value)) return "retry";
  return null;
}
export function encodeWav(chunks, sampleRate) {
  const length = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
  const buffer = new ArrayBuffer(44 + length * 2),
    view = new DataView(buffer);
  const write = (offset, text) =>
    [...text].forEach((letter, index) =>
      view.setUint8(offset + index, letter.charCodeAt(0)),
    );
  write(0, "RIFF");
  view.setUint32(4, 36 + length * 2, true);
  write(8, "WAVE");
  write(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, "data");
  view.setUint32(40, length * 2, true);
  let offset = 44;
  for (const chunk of chunks)
    for (const sample of chunk) {
      const value = Math.max(-1, Math.min(1, sample));
      view.setInt16(offset, value < 0 ? value * 32768 : value * 32767, true);
      offset += 2;
    }
  return new Blob([buffer], { type: "audio/wav" });
}
const abortError = () =>
  new DOMException("음성 입력을 취소했습니다.", "AbortError");
export const recognitionAvailable = () =>
  !!(globalThis.SpeechRecognition || globalThis.webkitSpeechRecognition);
export function browserListen({
  signal,
  onPartial = () => {},
  onLevel = () => {},
  onStopReady = () => {},
  maxSeconds = 60,
} = {}) {
  return new Promise((resolve, reject) => {
    const Recognition =
      globalThis.SpeechRecognition || globalThis.webkitSpeechRecognition;
    if (!Recognition) {
      reject(
        new Error(
          "이 브라우저는 실시간 음성 입력을 지원하지 않습니다. Chrome·Edge 등 지원 브라우저를 사용하거나 실제 STT 서버에 연결해 주세요.",
        ),
      );
      return;
    }
    const recognition = new Recognition();
    let text = "",
      settled = false,
      timer,
      hardTimer;
    recognition.lang = "ko-KR";
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;
    const cleanup = () => {
      clearTimeout(timer);
      clearTimeout(hardTimer);
      signal?.removeEventListener("abort", abort);
      recognition.onresult = null;
      recognition.onerror = null;
      recognition.onend = null;
      recognition.onspeechend = null;
      onLevel(0);
    };
    const finish = (error) => {
      if (settled) return;
      settled = true;
      cleanup();
      if (error) reject(error);
      else if (text.trim()) resolve(text.trim());
      else
        reject(
          new Error(
            "말소리를 인식하지 못했습니다. 마이크 가까이에서 다시 말해 주세요.",
          ),
        );
    };
    const abort = () => {
      try {
        recognition.abort();
      } finally {
        finish(abortError());
      }
    };
    recognition.onresult = (event) => {
      text = Array.from(event.results)
        .map((result) => result[0].transcript)
        .join(" ");
      onPartial(text);
      onLevel(0.6);
    };
    recognition.onspeechend = () => recognition.stop();
    recognition.onerror = (event) =>
      finish(
        new Error(
          {
            "not-allowed":
              "마이크 권한이 필요합니다. 브라우저의 마이크 접근을 허용해 주세요.",
            "audio-capture":
              "마이크를 찾을 수 없습니다. 장치 연결을 확인해 주세요.",
            network:
              "브라우저 음성 인식에 연결하지 못했습니다. 인터넷 연결을 확인하거나 서버 모드를 사용해 주세요.",
            "no-speech": "말소리가 들리지 않았습니다. 다시 말해 주세요.",
            aborted: "음성 입력이 중단됐습니다.",
          }[event.error] || "음성 인식에 실패했습니다. 다시 시도해 주세요.",
        ),
      );
    recognition.onend = () => finish();
    if (signal?.aborted) {
      finish(abortError());
      return;
    }
    signal?.addEventListener("abort", abort, { once: true });
    try {
      recognition.start();
      onStopReady(() => recognition.stop());
      timer = setTimeout(() => recognition.stop(), maxSeconds * 1000);
      hardTimer = setTimeout(
        () => {
          try {
            recognition.abort();
          } finally {
            finish(
              new Error("음성 인식 시간이 초과됐습니다. 다시 말해 주세요."),
            );
          }
        },
        (maxSeconds + 3) * 1000,
      );
    } catch (error) {
      finish(error);
    }
  });
}
export async function recordAudio({
  signal,
  onLevel = () => {},
  onStopReady = () => {},
  maxSeconds = 60,
} = {}) {
  if (!globalThis.isSecureContext || !navigator.mediaDevices?.getUserMedia)
    throw new Error(
      "음성 입력에는 HTTPS 또는 localhost와 마이크 접근 권한이 필요합니다.",
    );
  if (signal?.aborted) throw abortError();
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1,
      },
    });
  } catch (error) {
    throw new Error(
      error.name === "NotAllowedError"
        ? "마이크 접근이 차단됐습니다. 브라우저 권한을 확인해 주세요."
        : "마이크를 사용할 수 없습니다. 장치를 확인해 주세요.",
    );
  }
  if (signal?.aborted) {
    stream.getTracks().forEach((track) => track.stop());
    throw abortError();
  }
  return new Promise(async (resolve, reject) => {
    let context,
      source,
      analyser,
      recorder,
      processor,
      mute,
      interval,
      deadline,
      url,
      settled = false,
      speech = false,
      lastSpeech = Date.now(),
      started = Date.now(),
      noise = 0.008;
    let chunks = [],
      pcm = [];
    const cleanup = () => {
      clearInterval(interval);
      clearTimeout(deadline);
      signal?.removeEventListener("abort", abort);
      if (recorder) {
        recorder.ondataavailable = null;
        recorder.onstop = null;
        recorder.onerror = null;
        if (recorder.state !== "inactive") recorder.stop();
      }
      if (processor) {
        processor.port.onmessage = null;
        processor.disconnect();
      }
      source?.disconnect();
      mute?.disconnect();
      stream.getTracks().forEach((track) => track.stop());
      context?.close().catch(() => {});
      if (url) URL.revokeObjectURL(url);
      onLevel(0);
    };
    const finish = (error, blob) => {
      if (settled) return;
      settled = true;
      cleanup();
      chunks = [];
      pcm = [];
      if (error) reject(error);
      else if (!speech)
        reject(
          new Error(
            "말소리를 확인하지 못했습니다. 소음이 적은 방향으로 마이크를 가까이하고 다시 말해 주세요.",
          ),
        );
      else if (!blob?.size) reject(new Error("녹음된 음성이 없습니다."));
      else if (blob.size > 10 * 1024 * 1024)
        reject(new Error("음성이 10MB를 초과했습니다. 짧게 다시 말해 주세요."));
      else resolve(blob);
    };
    const stop = () => {
      if (settled) return;
      if (recorder && recorder.state !== "inactive") recorder.stop();
      else finish(null, encodeWav(pcm, context.sampleRate));
    };
    const abort = () => finish(abortError());
    signal?.addEventListener("abort", abort, { once: true });
    try {
      const AudioContext =
        globalThis.AudioContext || globalThis.webkitAudioContext;
      context = new AudioContext();
      await context.resume();
      if (settled) return;
      source = context.createMediaStreamSource(stream);
      analyser = context.createAnalyser();
      analyser.fftSize = 2048;
      source.connect(analyser);
      const mime = ["audio/webm;codecs=opus", "audio/webm"].find((type) =>
        globalThis.MediaRecorder?.isTypeSupported(type),
      );
      if (mime) {
        recorder = new MediaRecorder(stream, {
          mimeType: mime,
          audioBitsPerSecond: 64000,
        });
        recorder.ondataavailable = (event) => {
          if (event.data.size) chunks.push(event.data);
        };
        recorder.onerror = () =>
          finish(new Error("녹음 중 오류가 발생했습니다."));
        recorder.onstop = () => finish(null, new Blob(chunks, { type: mime }));
        recorder.start(250);
      } else {
        if (!context.audioWorklet)
          throw new Error(
            "이 브라우저에서 지원하는 WebM·WAV 녹음 방식이 없습니다.",
          );
        url = URL.createObjectURL(
          new Blob(
            [
              `class PcmCapture extends AudioWorkletProcessor { process(inputs){const channel=inputs[0]?.[0];if(channel)this.port.postMessage(channel.slice());return true;} } registerProcessor('pcm-capture',PcmCapture);`,
            ],
            { type: "text/javascript" },
          ),
        );
        await context.audioWorklet.addModule(url);
        if (settled) return;
        processor = new AudioWorkletNode(context, "pcm-capture");
        processor.port.onmessage = (event) => pcm.push(event.data);
        mute = context.createGain();
        mute.gain.value = 0;
        source.connect(processor);
        processor.connect(mute);
        mute.connect(context.destination);
      }
      onStopReady(stop);
      const samples = new Float32Array(analyser.fftSize);
      interval = setInterval(() => {
        analyser.getFloatTimeDomainData(samples);
        const rms = Math.sqrt(
          samples.reduce((sum, x) => sum + x * x, 0) / samples.length,
        );
        onLevel(Math.min(1, rms * 8));
        const elapsed = Date.now() - started;
        if (elapsed < 350) noise = 0.8 * noise + 0.2 * rms;
        if (rms > Math.max(0.018, noise * 2.2)) {
          speech = true;
          lastSpeech = Date.now();
        }
        if (
          (speech && Date.now() - lastSpeech > 1400 && elapsed > 1400) ||
          (!speech && elapsed > 8000)
        )
          stop();
      }, 100);
      deadline = setTimeout(stop, maxSeconds * 1000);
    } catch (error) {
      finish(error);
    }
  });
}
export function speakLocally(text, { signal } = {}) {
  const voice = globalThis.speechSynthesis
    ?.getVoices()
    .find((v) => v.localService && v.lang.startsWith("ko"));
  if (!voice || !globalThis.SpeechSynthesisUtterance)
    return Promise.resolve(false);
  return new Promise((resolve) => {
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.voice = voice;
    utterance.lang = "ko-KR";
    utterance.rate = 1.05;
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      clearTimeout(timeout);
      signal?.removeEventListener("abort", abort);
      resolve(true);
    };
    const abort = () => {
      speechSynthesis.cancel();
      finish();
    };
    utterance.onend = finish;
    utterance.onerror = finish;
    const timeout = setTimeout(
      () => {
        speechSynthesis.cancel();
        finish();
      },
      Math.max(5000, text.length * 300),
    );
    if (signal?.aborted) {
      finish();
      return;
    }
    signal?.addEventListener("abort", abort, { once: true });
    speechSynthesis.speak(utterance);
  });
}
