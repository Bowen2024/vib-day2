export const MIN_DURATION_MS = 1000;
export const MAX_DURATION_MS = 60_000;
export const MAX_FILE_BYTES = 5 * 1024 * 1024;

export function detectRecordingMimeType() {
  if (typeof MediaRecorder === "undefined" || typeof MediaRecorder.isTypeSupported !== "function") {
    return null;
  }

  const candidates = ["audio/webm;codecs=opus", "audio/webm"];
  return candidates.find((type) => MediaRecorder.isTypeSupported(type)) ?? null;
}

export function fileExtensionForMime(mimeType) {
  return mimeType?.includes("webm") ? "webm" : "webm";
}

export function formatDuration(ms) {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

export function formatBytes(bytes) {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}
