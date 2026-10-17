"use client";
import { useEffect, useState } from "react";
import { api, EMOTION_WORD, LABELS, type Distribution, type EmotionProfile } from "@/lib/api";
import type { FaceStatus } from "@/lib/useFaceEmotion";
import { Orb } from "./Orb";

const STEPS: [string, string][] = [
  ["check_in", "Check in"],
  ["situation", "The situation"],
  ["automatic_thought", "The thought"],
  ["emotion_rating", "Rate the feeling"],
  ["distortions", "Thinking traps"],
  ["evidence", "Weigh the evidence"],
  ["balanced_thought", "A balanced thought"],
  ["rerate", "Rate it again"],
  ["action_plan", "One small step"],
  ["closing", "Wrap up"],
];
const SIDE: Record<string, string> = {
  grounding: "Paused for a grounding exercise",
  supportive_listening: "Paused to just listen",
};
const CAM_TEXT: Record<FaceStatus, string> = {
  off: "Camera off",
  loading: "Loading the expression model…",
  running: "Reading expression on this device",
  "no-face": "Can't see a face. Check your lighting.",
  denied: "Camera blocked. Allow it in your browser settings.",
  error: "The camera couldn't start.",
};

type Face = { videoRef: React.RefObject<HTMLVideoElement | null>; status: FaceStatus; live: Distribution | null };

export function InsightPanel({ profile, stage, resume, cameraAllowed, cameraOn, onCamera, face }: {
  profile: EmotionProfile | null;
  stage: string;
  resume: string | null;
  cameraAllowed: boolean;
  cameraOn: boolean;
  onCamera: (on: boolean) => void;
  face: Face;
}) {
  const [resources, setResources] = useState<{ name: string; contact: string }[]>([]);
  useEffect(() => {
    api<{ resources: { name: string; contact: string }[] }>("/resources").then((d) => setResources(d.resources)).catch(() => {});
  }, []);

  const dominant = profile?.dominant ?? "neutral";
  const live = face.live;
  const stageIdx = STEPS.findIndex(([k]) => k === (SIDE[stage] ? resume ?? "check_in" : stage));

  return (
    <aside className="panel" aria-label="How the conversation is going">
      <section>
        <h2>How you seem right now</h2>
        <div className="feel">
          <Orb emotion={dominant} arousal={profile?.arousal ?? 0} />
          <div>
            <div className="feel-word">{EMOTION_WORD[dominant]}</div>
            <div className="small muted">
              {profile
                ? profile.distress_trend === "rising" ? "Feeling heavier than before"
                  : profile.distress_trend === "easing" ? "Easing a little"
                  : "Holding steady"
                : "Arna will notice as you talk"}
            </div>
          </div>
        </div>
        <div className="bars">
          {LABELS.map((k) => {
            const v = profile?.emotions?.[k] ?? 0;
            const f = live?.[k];
            return (
              <div className="bar" key={k}>
                <span>{EMOTION_WORD[k]}</span>
                <div className="bar-track">
                  <div className="bar-fill" style={{ width: `${Math.round(v * 100)}%`, background: `var(--e-${k})` }} />
                  {f !== undefined && <div className="bar-face" style={{ left: `calc(${Math.round(f * 100)}% - 1px)` }} />}
                </div>
                <span className="muted">{Math.round(v * 100)}%</span>
              </div>
            );
          })}
        </div>
        <div className="legend">
          <span><i style={{ background: "var(--e-fearful)" }} />Words and expression combined</span>
          {live && <span><i style={{ background: "var(--ink)", width: 3 }} />Expression now</span>}
        </div>
      </section>

      <section>
        <div className="toggle">
          <h2 style={{ margin: 0 }}>Camera</h2>
          {cameraAllowed ? (
            <button className="quiet small" onClick={() => onCamera(!cameraOn)} aria-pressed={cameraOn}>
              {cameraOn ? "Turn off" : "Turn on"}
            </button>
          ) : null}
        </div>
        {cameraAllowed ? (
          cameraOn && (
            <div className="cam">
              <video ref={face.videoRef} muted playsInline aria-label="Your camera preview" />
              <span className="cam-status">{CAM_TEXT[face.status]}</span>
            </div>
          )
        ) : (
          <p className="small muted" style={{ marginTop: 8 }}>
            You chose not to use the camera. Arna is reading the feeling in your words only.
          </p>
        )}
      </section>

      <section>
        <h2>Thought record</h2>
        {SIDE[stage] && <p className="side-note" style={{ marginBottom: 10 }}>{SIDE[stage]}</p>}
        <ol className="steps">
          {STEPS.map(([k, label], i) => (
            <li key={k} className={i < stageIdx ? "done" : i === stageIdx ? "now" : ""} aria-current={i === stageIdx ? "step" : undefined}>
              {label}
            </li>
          ))}
        </ol>
      </section>

      <section>
        <h2>Need help now?</h2>
        <ul className="help-list">
          {resources.map((r) => (
            <li key={r.name}><strong>{r.contact}</strong><span className="muted">{r.name}</span></li>
          ))}
        </ul>
      </section>
    </aside>
  );
}
