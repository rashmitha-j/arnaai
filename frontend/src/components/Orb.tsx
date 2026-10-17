import type { CSSProperties } from "react";
import type { EmotionLabel } from "@/lib/api";

/** Breathing orb: colour follows the dominant emotion, breathing pace follows arousal. */
export function Orb({ emotion = "neutral", arousal = 0, className = "", label }: {
  emotion?: EmotionLabel;
  arousal?: number;
  className?: string;
  label?: string;
}) {
  const speed = Math.max(2.6, 7 - Math.max(0, arousal) * 5);
  const style = { "--c": `var(--e-${emotion})`, "--speed": `${speed.toFixed(1)}s` } as CSSProperties;
  return <div className={`orb ${className}`} style={style} role={label ? "img" : undefined} aria-label={label} aria-hidden={label ? undefined : true} />;
}
