"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type EmotionProfile, type Message, type TurnResult } from "@/lib/api";
import { useFaceEmotion } from "@/lib/useFaceEmotion";
import { TopBar } from "@/components/TopBar";
import { ConsentGate } from "@/components/ConsentGate";
import { InsightPanel } from "@/components/InsightPanel";

type Session = { id: string; token: string; camera: boolean };
const KEY = "arna.session";

export default function ChatPage() {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [mode, setMode] = useState<"ai" | "human">("ai");
  const [profile, setProfile] = useState<EmotionProfile | null>(null);
  const [cbt, setCbt] = useState<{ stage: string; resume?: string | null }>({ stage: "check_in" });
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [cameraOn, setCameraOn] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  const face = useFaceEmotion(cameraOn && !!session?.camera);

  // Restore a session from this tab.
  useEffect(() => {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return setReady(true);
    const s: Session = JSON.parse(raw);
    api<{ messages: Message[]; mode: "ai" | "human"; emotion: EmotionProfile | null; cbt: { stage: string; resume_stage?: string | null } }>(
      `/sessions/${s.id}`, { token: s.token })
      .then((d) => {
        setSession(s);
        setMessages(d.messages);
        setMode(d.mode);
        setProfile(d.emotion);
        setCbt({ stage: d.cbt.stage || "check_in", resume: d.cbt.resume_stage });
        setCameraOn(s.camera);
      })
      .catch(() => sessionStorage.removeItem(KEY))
      .finally(() => setReady(true));
  }, []);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, sending]);

  const merge = useCallback((incoming: Message[]) => {
    setMessages((prev) => {
      const seen = new Set(prev.map((m) => m.id));
      const add = incoming.filter((m) => !seen.has(m.id));
      return add.length ? [...prev, ...add].sort((a, b) => a.created_at - b.created_at) : prev;
    });
  }, []);

  // While a human counselor owns the conversation, poll for their messages.
  useEffect(() => {
    if (!session || mode !== "human") return;
    const t = setInterval(async () => {
      try {
        const last = messages.length ? messages[messages.length - 1].created_at : 0;
        const d = await api<{ messages: Message[]; mode: "ai" | "human" }>(
          `/sessions/${session.id}/messages?after=${last}`, { token: session.token });
        merge(d.messages);
        setMode(d.mode);
      } catch { /* keep polling */ }
    }, 3000);
    return () => clearInterval(t);
  }, [session, mode, messages, merge]);

  async function start(camera: boolean) {
    const d = await api<{ session_id: string; token: string; messages: Message[] }>("/sessions", {
      method: "POST",
      body: JSON.stringify({ accepted_terms: true, camera_consent: camera }),
    });
    const s = { id: d.session_id, token: d.token, camera };
    sessionStorage.setItem(KEY, JSON.stringify(s));
    setSession(s);
    setMessages(d.messages);
    setCameraOn(camera);
  }

  async function send() {
    const text = draft.trim();
    if (!text || !session || sending) return;
    setError("");
    setSending(true);
    setDraft("");
    const temp: Message = { id: `tmp-${Date.now()}`, role: "user", content: text, created_at: Date.now() / 1000 };
    setMessages((m) => [...m, temp]);
    const { face: faceDist, samples } = face.takeSample();
    try {
      const r = await api<TurnResult>(`/sessions/${session.id}/messages`, {
        method: "POST",
        token: session.token,
        body: JSON.stringify({ text, face: faceDist, face_samples: samples }),
      });
      setMessages((m) => [...m.filter((x) => x.id !== temp.id), r.user_message, ...(r.reply ? [r.reply] : [])]);
      setMode(r.mode);
      setProfile(r.emotion);
      setCbt({ stage: r.cbt.stage, resume: r.cbt.resume_stage });
    } catch (e) {
      setMessages((m) => m.filter((x) => x.id !== temp.id));
      setDraft(text);
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  }

  async function endAndDelete() {
    if (!session) return;
    if (!confirm("Delete this conversation? This permanently removes your messages.")) return;
    try {
      await api(`/sessions/${session.id}`, { method: "DELETE", token: session.token });
    } catch { /* still clear locally */ }
    sessionStorage.removeItem(KEY);
    setSession(null);
    setMessages([]);
    setProfile(null);
    setCameraOn(false);
    setMode("ai");
  }

  if (!ready) return null;

  return (
    <main className="wrap">
      <TopBar>
        {session && <button className="quiet small" onClick={endAndDelete}>End and delete chat</button>}
      </TopBar>

      {!session ? (
        <ConsentGate onStart={start} />
      ) : (
        <div className="chat-shell">
          <section className="chat-main" aria-label="Conversation">
            <div className="chat-log" ref={logRef} aria-live="polite">
              {messages.map((m) => (
                <div key={m.id} className={`bubble ${m.role} ${m.meta?.stage === "safety" ? "safety" : ""}`}>
                  {m.role === "counselor" && <span className="who">Counselor</span>}
                  {m.content}
                </div>
              ))}
              {sending && mode === "ai" && <div className="typing">Arna is thinking…</div>}
            </div>
            {mode === "human" && (
              <div className="banner human" role="status">
                A human counselor has been asked to join. Arna is paused, and your messages go straight to them.
              </div>
            )}
            {error && <div className="banner error" role="alert">{error}</div>}
            <form className="composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
              <label htmlFor="msg" className="sr-only">Your message</label>
              <textarea
                id="msg"
                rows={2}
                value={draft}
                placeholder="Write what's on your mind…"
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
                }}
              />
              <button type="submit" disabled={sending || !draft.trim()}>Send</button>
            </form>
          </section>

          <InsightPanel
            profile={profile}
            stage={cbt.stage}
            resume={cbt.resume ?? null}
            cameraAllowed={session.camera}
            cameraOn={cameraOn}
            onCamera={setCameraOn}
            face={face}
          />
        </div>
      )}
    </main>
  );
}
