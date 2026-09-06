import { useMemo, useState } from "react";
import { detectRecordingMimeType } from "./audio/recording.js";
import RecordButton from "./components/RecordButton.jsx";
import RecordingPreview from "./components/RecordingPreview.jsx";

export default function App() {
  const mimeType = useMemo(() => detectRecordingMimeType(), []);
  const [city, setCity] = useState("杭州");
  const [clip, setClip] = useState(null);
  const [error, setError] = useState("");

  return (
    <main className="page">
      <section className="card">
        <p className="eyebrow">本地第一版</p>
        <h1>语音约碰面地点</h1>
        <p className="lead">
          按住录音，说出两个人所在的地点和想去的店。本轮只做本地录音，不识别、不找店。
        </p>

        <label className="field">
          <span>城市</span>
          <input
            value={city}
            onChange={(event) => setCity(event.target.value)}
            maxLength={20}
            autoComplete="off"
          />
        </label>

        {mimeType ? (
          <RecordButton
            mimeType={mimeType}
            onComplete={(nextClip) => {
              setError("");
              setClip(nextClip);
            }}
            onError={setError}
            onDiscard={() => setClip(null)}
          />
        ) : (
          <p className="error">当前浏览器不支持 WebM/Opus 录音，请使用最新版 Chrome 或 Edge。</p>
        )}

        {error ? <p className="error">{error}</p> : null}
        {clip ? <RecordingPreview clip={clip} /> : null}
      </section>
    </main>
  );
}
