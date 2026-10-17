"use client";
import { useState } from "react";

export function ConsentGate({ onStart }: { onStart: (camera: boolean) => Promise<void> }) {
  const [terms, setTerms] = useState(false);
  const [camera, setCamera] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  return (
    <section className="consent">
      <h1>Before we start</h1>
      <ul>
        <li>Arna is an AI companion, not a therapist, and can make mistakes.</li>
        <li>Your messages are encrypted when stored. You can delete the conversation at any time.</li>
        <li>If you say something that suggests you may be unsafe, a trained human counselor will read the conversation and join it.</li>
        <li>In an emergency, call 112 or Tele-MANAS on 14416.</li>
      </ul>

      <label className="check">
        <input type="checkbox" checked={terms} onChange={(e) => setTerms(e.target.checked)} />
        <span>I understand and want to continue.</span>
      </label>
      <label className="check">
        <input type="checkbox" checked={camera} onChange={(e) => setCamera(e.target.checked)} />
        <span>
          Use my camera to help Arna notice how I&apos;m feeling. <span className="muted">Optional. The video is
          analysed on this device; only an emotion estimate is sent.</span>
        </span>
      </label>

      {error && <p className="banner error" style={{ margin: "12px 0" }}>{error}</p>}
      <button
        disabled={!terms || busy}
        onClick={async () => {
          setBusy(true);
          setError("");
          try { await onStart(camera); } catch (e) { setError((e as Error).message); setBusy(false); }
        }}
      >
        {busy ? "Starting…" : "Start the conversation"}
      </button>
    </section>
  );
}
