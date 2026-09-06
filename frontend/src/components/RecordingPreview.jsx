import { useEffect, useState } from "react";
import { fileExtensionForMime, formatBytes, formatDuration } from "../audio/recording.js";

export default function RecordingPreview({ clip }) {
  const [objectUrl, setObjectUrl] = useState("");

  useEffect(() => {
    const url = URL.createObjectURL(clip.blob);
    setObjectUrl(url);
    return () => {
      URL.revokeObjectURL(url);
    };
  }, [clip]);

  const filename = `recording.${fileExtensionForMime(clip.mimeType)}`;

  return (
    <section className="preview">
      <h2>本地试听</h2>
      {objectUrl ? <audio controls src={objectUrl} /> : null}
      <p className="meta">
        格式 {clip.mimeType} · 大小 {formatBytes(clip.size)} · 时长 {formatDuration(clip.durationMs)}
      </p>
      {objectUrl ? (
        <a className="download" href={objectUrl} download={filename}>
          下载录音文件（临时，供后续上传测试）
        </a>
      ) : null}
    </section>
  );
}
