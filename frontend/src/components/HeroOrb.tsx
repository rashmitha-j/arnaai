"use client";
import { useEffect, useState } from "react";
import { EMOTION_WORD, type EmotionLabel } from "@/lib/api";
import { Orb } from "./Orb";

const CYCLE: { e: EmotionLabel; a: number }[] = [
  { e: "fearful", a: 0.7 },
  { e: "sad", a: 0.2 },
  { e: "neutral", a: 0.1 },
  { e: "happy", a: 0.35 },
];

export function HeroOrb() {
  const [i, setI] = useState(0);
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const t = setInterval(() => setI((x) => (x + 1) % CYCLE.length), 3600);
    return () => clearInterval(t);
  }, []);
  const s = CYCLE[i];
  return (
    <div>
      <Orb emotion={s.e} arousal={s.a} className="hero-orb" />
      <p className="hero-caption muted small" aria-live="off">
        Arna noticing: <strong style={{ color: "var(--ink)" }}>{EMOTION_WORD[s.e].toLowerCase()}</strong>
      </p>
    </div>
  );
}
