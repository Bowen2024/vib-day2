import { useEffect, useRef, useState } from "react";
import {
  MAX_DURATION_MS,
  MAX_FILE_BYTES,
  MIN_DURATION_MS,
  formatDuration,
} from "../audio/recording.js";

function microphoneErrorMessage(error) {
  const name = error?.name ?? "";
  if (name === "NotAllowedError" || name === "PermissionDeniedError") {
    return "没有获得麦克风权限，请在浏览器中允许后重试。";
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError") {
    return "没有找到可用的麦克风。";
  }
  return "录音失败，请重试。";
}

function releaseStream(stream) {
  if (!stream) {
    return;
  }
  for (const track of stream.getTracks()) {
    track.stop();
  }
}

export default function RecordButton({ mimeType, disabled, onComplete, onError, onDiscard }) {
  const [phase, setPhase] = useState("idle");
  const [elapsedMs, setElapsedMs] = useState(0);
  const sessionRef = useRef(null);

  if (sessionRef.current === null) {
    sessionRef.current = {
      generation: 0,
      pending: false,
      abort: false,
      cancel: false,
      stream: null,
      recorder: null,
      chunks: [],
      startedAt: 0,
      maxTimer: null,
      tickTimer: null,
    };
  }

  function clearTimers(session) {
    if (session.maxTimer) {
      window.clearTimeout(session.maxTimer);
      session.maxTimer = null;
    }
    if (session.tickTimer) {
      window.clearInterval(session.tickTimer);
      session.tickTimer = null;
    }
  }

  function requestStop(session) {
    clearTimers(session);
    session.pending = false;
    const recorder = session.recorder;
    if (recorder) {
      if (recorder.state !== "inactive") {
        try {
          recorder.stop();
        } catch {
          // already stopping
        }
      }
      return;
    }
    releaseStream(session.stream);
    session.stream = null;
    session.chunks = [];
    setPhase("idle");
    setElapsedMs(0);
  }

  useEffect(() => {
    return () => {
      const session = sessionRef.current;
      session.generation += 1;
      session.abort = true;
      requestStop(session);
    };
    // Unmount is the only time this cleanup should run.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function startRecording(event) {
    if (disabled || !mimeType || event.button !== 0) {
      return;
    }

    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);

    const session = sessionRef.current;
    session.generation += 1;
    const generation = session.generation;
    session.abort = false;
    session.cancel = false;
    session.pending = true;
    session.chunks = [];
    onDiscard?.();
    onError?.("");
    setElapsedMs(0);
    setPhase("requesting");

    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
      if (generation !== session.generation) {
        return;
      }
      session.pending = false;
      setPhase("idle");
      onError?.(microphoneErrorMessage(error));
      return;
    }

    if (generation !== session.generation || session.abort || session.cancel) {
      releaseStream(stream);
      session.pending = false;
      setPhase("idle");
      return;
    }

    let recorder;
    try {
      recorder = new MediaRecorder(stream, { mimeType });
    } catch {
      releaseStream(stream);
      session.pending = false;
      setPhase("idle");
      onError?.("当前浏览器无法按检测到的格式录音，请更换最新版 Chrome 或 Edge。");
      return;
    }

    session.stream = stream;
    session.recorder = recorder;
    session.startedAt = Date.now();
    session.pending = false;

    recorder.ondataavailable = (dataEvent) => {
      if (dataEvent.data && dataEvent.data.size > 0) {
        session.chunks.push(dataEvent.data);
      }
    };

    recorder.onerror = () => {
      if (generation !== session.generation) {
        return;
      }
      session.abort = true;
      onError?.("录音失败，请重试。");
      requestStop(session);
    };

    recorder.onstop = () => {
      const current = sessionRef.current;
      const chunks = current.chunks;
      const startedAt = current.startedAt;
      const wasCancel = current.cancel || current.abort;
      releaseStream(current.stream);
      current.stream = null;
      current.recorder = null;
      current.chunks = [];
      clearTimers(current);
      setPhase("idle");
      setElapsedMs(0);

      if (generation !== current.generation || wasCancel) {
        return;
      }

      const durationMs = Date.now() - startedAt;
      const blob = new Blob(chunks, { type: mimeType });

      if (durationMs < MIN_DURATION_MS) {
        onError?.("录音需在 1 到 60 秒之间，请重新按住录制。");
        return;
      }
      if (blob.size > MAX_FILE_BYTES) {
        onError?.("录音文件超过 5MB，请缩短录音后再试。");
        return;
      }
      if (blob.size === 0) {
        onError?.("录音失败，请重试。");
        return;
      }

      onComplete({
        blob,
        mimeType,
        durationMs: Math.min(durationMs, MAX_DURATION_MS),
        size: blob.size,
      });
    };

    try {
      recorder.start();
    } catch {
      releaseStream(stream);
      session.stream = null;
      session.recorder = null;
      setPhase("idle");
      onError?.("录音失败，请重试。");
      return;
    }

    setPhase("recording");
    session.tickTimer = window.setInterval(() => {
      setElapsedMs(Math.min(Date.now() - session.startedAt, MAX_DURATION_MS));
    }, 100);
    session.maxTimer = window.setTimeout(() => {
      if (generation !== session.generation) {
        return;
      }
      requestStop(session);
    }, MAX_DURATION_MS);
  }

  function stopRecording() {
    const session = sessionRef.current;
    if (session.pending) {
      session.abort = true;
      return;
    }
    if (!session.recorder) {
      return;
    }
    requestStop(session);
  }

  function cancelRecording(event) {
    event?.preventDefault();
    event?.stopPropagation();
    const session = sessionRef.current;
    session.cancel = true;
    session.abort = true;
    onDiscard?.();
    if (session.pending) {
      return;
    }
    requestStop(session);
  }

  useEffect(() => {
    function onKeyDown(event) {
      if (event.key === "Escape") {
        cancelRecording(event);
      }
    }

    window.addEventListener("pointerup", stopRecording);
    window.addEventListener("pointercancel", cancelRecording);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pointerup", stopRecording);
      window.removeEventListener("pointercancel", cancelRecording);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, []);

  const recording = phase === "recording";

  return (
    <div className="record-block">
      <button
        type="button"
        className={recording ? "record-button is-recording" : "record-button"}
        disabled={disabled}
        onPointerDown={startRecording}
        onContextMenu={(event) => event.preventDefault()}
      >
        {recording ? "松开结束" : "按住录音"}
      </button>
      {recording ? (
        <div className="record-live">
          <p className="record-timer">录音中 {formatDuration(elapsedMs)}</p>
          <button
            type="button"
            className="text-button"
            onPointerDown={cancelRecording}
          >
            取消
          </button>
        </div>
      ) : (
        <p className="hint">
          按住说话，松开结束。移出按钮后松开也会结束并保存。按 Esc 或点取消可丢弃。
        </p>
      )}
    </div>
  );
}
