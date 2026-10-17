"use client";
// Real-time facial expression analysis that runs entirely in the browser.
// Video frames never leave the device: we only send a 7-number expression
// distribution (averaged over the frames captured while the user typed).
import { useCallback, useEffect, useRef, useState } from "react";
import type { Distribution, EmotionLabel } from "./api";
import { LABELS } from "./api";

const MODEL_URL = process.env.NEXT_PUBLIC_FACE_MODEL_URL || "https://cdn.jsdelivr.net/npm/@vladmandic/face-api@1.7.15/model";
const SAMPLE_MS = 400;

export type FaceStatus = "off" | "loading" | "running" | "no-face" | "denied" | "error";

export function useFaceEmotion(enabled: boolean) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [status, setStatus] = useState<FaceStatus>("off");
  const [live, setLive] = useState<Distribution | null>(null);
  const acc = useRef<{ sum: Record<EmotionLabel, number>; n: number }>({ sum: zero(), n: 0 });

  useEffect(() => {
    if (!enabled) {
      setStatus("off");
      setLive(null);
      return;
    }
    let stream: MediaStream | null = null;
    let timer: ReturnType<typeof setInterval> | null = null;
    let cancelled = false;

    (async () => {
      setStatus("loading");
      try {
        const faceapi = await import("@vladmandic/face-api");
        await Promise.all([
          faceapi.nets.tinyFaceDetector.loadFromUri(MODEL_URL),
          faceapi.nets.faceExpressionNet.loadFromUri(MODEL_URL),
        ]);
        stream = await navigator.mediaDevices.getUserMedia({ video: { width: 320, height: 240, facingMode: "user" }, audio: false });
        if (cancelled || !videoRef.current) return;
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
        setStatus("running");
        const opts = new faceapi.TinyFaceDetectorOptions({ inputSize: 224, scoreThreshold: 0.45 });

        timer = setInterval(async () => {
          const v = videoRef.current;
          if (!v || v.readyState < 2) return;
          const r = await faceapi.detectSingleFace(v, opts).withFaceExpressions();
          if (!r) {
            setStatus("no-face");
            return;
          }
          setStatus("running");
          const d: Distribution = {};
          for (const k of LABELS) {
            const p = (r.expressions as unknown as Record<string, number>)[k] ?? 0;
            d[k] = p;
            acc.current.sum[k] += p;
          }
          acc.current.n += 1;
          setLive(d);
        }, SAMPLE_MS);
      } catch (e) {
        const name = (e as Error)?.name;
        setStatus(name === "NotAllowedError" || name === "SecurityError" ? "denied" : "error");
      }
    })();

    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, [enabled]);

  /** Average of frames since the last call, then reset. */
  const takeSample = useCallback((): { face: Distribution | null; samples: number } => {
    const { sum, n } = acc.current;
    acc.current = { sum: zero(), n: 0 };
    if (!n) return { face: null, samples: 0 };
    const face: Distribution = {};
    for (const k of LABELS) face[k] = +(sum[k] / n).toFixed(4);
    return { face, samples: n };
  }, []);

  return { videoRef, status, live, takeSample };
}

function zero(): Record<EmotionLabel, number> {
  return { neutral: 0, happy: 0, sad: 0, angry: 0, fearful: 0, disgusted: 0, surprised: 0 };
}
