export type Role = "user" | "assistant" | "counselor";

export interface Message {
  id: string;
  role: Role;
  content: string;
  created_at: number;
  meta?: Record<string, unknown>;
}

export type EmotionLabel = "neutral" | "happy" | "sad" | "angry" | "fearful" | "disgusted" | "surprised";
export type Distribution = Partial<Record<EmotionLabel, number>>;

export interface EmotionProfile {
  emotions: Distribution;
  dominant: EmotionLabel;
  confidence: number;
  valence: number;
  arousal: number;
  distress: number;
  distress_trend: "rising" | "easing" | "stable";
  incongruence: number | null;
  modalities?: { text: Distribution; face: Distribution | null; weights: { text: number; face: number } };
}

export interface TurnResult {
  mode: "ai" | "human";
  reply: Message | null;
  user_message: Message;
  cbt: { stage: string; resume_stage: string | null; progress: number; slots: Record<string, unknown>; distortions: string[] };
  emotion: EmotionProfile;
  risk: { level: string };
  escalation: { id: string; priority: string } | null;
}

export async function api<T>(path: string, init: RequestInit & { token?: string } = {}): Promise<T> {
  const { token, headers, ...rest } = init;
  const res = await fetch(`/api${path}`, {
    ...rest,
    headers: {
      "content-type": "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...(headers || {}),
    },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((data as { error?: string }).error || `Request failed (${res.status})`);
  return data as T;
}

export const LABELS: EmotionLabel[] = ["neutral", "happy", "sad", "angry", "fearful", "disgusted", "surprised"];

export const EMOTION_WORD: Record<EmotionLabel, string> = {
  neutral: "Steady",
  happy: "Light",
  sad: "Low",
  angry: "Frustrated",
  fearful: "Anxious",
  disgusted: "Uneasy",
  surprised: "Startled",
};
