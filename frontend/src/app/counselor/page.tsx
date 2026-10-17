"use client";
import { useCallback, useEffect, useState } from "react";
import { api, EMOTION_WORD, type EmotionProfile, type Message } from "@/lib/api";
import { TopBar } from "@/components/TopBar";
import { Orb } from "@/components/Orb";

interface Escalation {
  id: string;
  session_id: string;
  level: string;
  priority: "critical" | "urgent" | "review";
  status: "open" | "claimed" | "resolved";
  counselor: string;
  created_at: number;
  reason: { risk?: { reasons?: string[]; categories?: string[] } };
}
interface Detail {
  escalation: Escalation;
  session: { mode: string; status: string; risk_level: string; emotion: EmotionProfile | null; cbt: { stage?: string } | null };
  messages: Message[];
}

const KEY = "arna.counselor";
const PRIORITY_TEXT = { critical: "Critical", urgent: "Urgent", review: "Review" };
const ago = (t: number) => {
  const m = Math.round((Date.now() / 1000 - t) / 60);
  return m < 1 ? "just now" : m < 60 ? `${m} min ago` : `${Math.round(m / 60)} h ago`;
};

export default function CounselorPage() {
  const [auth, setAuth] = useState<{ token: string; name: string } | null>(null);
  const [form, setForm] = useState({ token: "", name: "" });
  const [tab, setTab] = useState<"active" | "resolved">("active");
  const [items, setItems] = useState<Escalation[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [reply, setReply] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const raw = sessionStorage.getItem(KEY);
    if (raw) setAuth(JSON.parse(raw));
  }, []);

  const headers = auth ? { "x-counselor-name": auth.name } : undefined;

  const loadQueue = useCallback(async () => {
    if (!auth) return;
    try {
      const all = await api<{ escalations: Escalation[] }>("/counselor/escalations", { token: auth.token, headers });
      const order = { critical: 0, urgent: 1, review: 2 };
      setItems(all.escalations
        .filter((e) => (tab === "resolved" ? e.status === "resolved" : e.status !== "resolved"))
        .sort((a, b) => order[a.priority] - order[b.priority] || b.created_at - a.created_at));
      setError("");
    } catch (e) {
      const msg = (e as Error).message;
      if (msg === "unauthorized") { sessionStorage.removeItem(KEY); setAuth(null); }
      setError(msg === "unauthorized" ? "That access code isn't valid." : msg);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth, tab]);

  const loadDetail = useCallback(async () => {
    if (!auth || !selected) return;
    try {
      setDetail(await api<Detail>(`/counselor/escalations/${selected}`, { token: auth.token, headers }));
    } catch (e) { setError((e as Error).message); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth, selected]);

  useEffect(() => {
    loadQueue();
    const t = setInterval(loadQueue, 5000);
    return () => clearInterval(t);
  }, [loadQueue]);

  useEffect(() => {
    setDetail(null);
    loadDetail();
    const t = setInterval(loadDetail, 3000);
    return () => clearInterval(t);
  }, [loadDetail]);

  async function sendReply() {
    if (!auth || !selected || !reply.trim()) return;
    try {
      await api(`/counselor/escalations/${selected}/messages`, {
        method: "POST", token: auth.token, headers, body: JSON.stringify({ text: reply.trim() }),
      });
      setReply("");
      loadDetail();
      loadQueue();
    } catch (e) { setError((e as Error).message); }
  }

  async function resolve(returnToAi: boolean) {
    if (!auth || !selected) return;
    try {
      await api(`/counselor/escalations/${selected}/resolve`, {
        method: "POST", token: auth.token, headers, body: JSON.stringify({ notes, return_to_ai: returnToAi }),
      });
      setNotes("");
      setSelected(null);
      loadQueue();
    } catch (e) { setError((e as Error).message); }
  }

  if (!auth) {
    return (
      <main className="wrap">
        <TopBar />
        <form
          className="consent"
          onSubmit={(e) => {
            e.preventDefault();
            const a = { token: form.token.trim(), name: form.name.trim() || "Counselor" };
            sessionStorage.setItem(KEY, JSON.stringify(a));
            setAuth(a);
          }}
        >
          <h1>Counselor sign-in</h1>
          <p className="muted" style={{ marginBottom: 18 }}>
            People who may be unsafe are routed here. You&apos;ll see their conversation and can reply in it directly.
          </p>
          <label className="small" htmlFor="cname">Your name, as people will see it</label>
          <input id="cname" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} style={{ margin: "6px 0 14px" }} />
          <label className="small" htmlFor="ctoken">Access code</label>
          <input id="ctoken" type="password" value={form.token} onChange={(e) => setForm({ ...form, token: e.target.value })} style={{ margin: "6px 0 18px" }} />
          <button type="submit" disabled={!form.token}>Sign in</button>
        </form>
      </main>
    );
  }

  const e = detail?.escalation;
  const prof = detail?.session.emotion;

  return (
    <main className="wrap">
      <TopBar>
        <span className="small muted">Signed in as {auth.name}</span>
        <button className="quiet small" onClick={() => { sessionStorage.removeItem(KEY); setAuth(null); }}>Sign out</button>
      </TopBar>
      {error && <p className="banner error" role="alert" style={{ margin: "0 0 12px" }}>{error}</p>}

      <div className="c-grid">
        <nav className="queue" aria-label="People waiting">
          <div className="queue-tabs">
            <button className={tab === "active" ? "" : "quiet"} onClick={() => setTab("active")}>Waiting</button>
            <button className={tab === "resolved" ? "" : "quiet"} onClick={() => setTab("resolved")}>Resolved</button>
          </div>
          {items.length === 0 && <p className="muted small" style={{ padding: 12 }}>{tab === "active" ? "No one is waiting. New alerts appear here automatically." : "Nothing resolved yet."}</p>}
          {items.map((it) => (
            <button key={it.id} className="queue-item" aria-current={it.id === selected} onClick={() => setSelected(it.id)}>
              <span className={`prio ${it.priority}`}>{PRIORITY_TEXT[it.priority]}</span>
              <span className="small muted">{ago(it.created_at)}</span>
              <div className="small" style={{ marginTop: 6 }}>
                {(it.reason.risk?.categories || []).map((c) => c.replace(/_/g, " ")).join(", ") || `Risk: ${it.level}`}
              </div>
              {it.status === "claimed" && <div className="small muted">With {it.counselor}</div>}
            </button>
          ))}
        </nav>

        <section className="case" aria-label="Conversation">
          {!detail || !e ? (
            <div className="empty"><p>Pick someone from the list to read their conversation and reply.</p></div>
          ) : (
            <>
              <div className="case-head">
                <div style={{ display: "flex", gap: 14, alignItems: "center" }}>
                  <Orb emotion={prof?.dominant ?? "neutral"} arousal={prof?.arousal ?? 0} className="case-orb" />
                  <div>
                    <h1><span className={`prio ${e.priority}`}>{PRIORITY_TEXT[e.priority]}</span>Risk level: {e.level}</h1>
                    <p className="small muted">
                      {prof ? `Seems ${EMOTION_WORD[prof.dominant].toLowerCase()}, distress ${Math.round(prof.distress * 100)}% (${prof.distress_trend})` : "No emotion data"}
                      {detail.session.mode === "human" ? ". Arna is paused." : ". Arna is still replying."}
                    </p>
                    {e.reason.risk?.reasons?.length ? (
                      <p className="small muted">Flagged because: {e.reason.risk.reasons.slice(0, 2).join("; ")}</p>
                    ) : null}
                  </div>
                </div>
              </div>

              <div className="chat-log">
                {detail.messages.map((m) => (
                  <div key={m.id} className={`bubble ${m.role} ${m.meta?.stage === "safety" ? "safety" : ""}`}>
                    <span className="who" style={{ color: m.role === "user" ? "inherit" : "var(--muted)", opacity: m.role === "user" ? 0.8 : 1 }}>
                      {m.role === "user" ? "Person" : m.role === "counselor" ? String(m.meta?.counselor ?? "Counselor") : "Arna"}
                    </span>
                    {m.content}
                  </div>
                ))}
              </div>

              {e.status !== "resolved" && (
                <div style={{ borderTop: "1.5px solid var(--line)", padding: "14px 18px 18px", display: "grid", gap: 10 }}>
                  <form className="composer" style={{ padding: 0, border: 0 }} onSubmit={(ev) => { ev.preventDefault(); sendReply(); }}>
                    <label htmlFor="reply" className="sr-only">Reply to this person</label>
                    <textarea id="reply" rows={2} placeholder="Reply as yourself. Arna stays paused while you're here." value={reply} onChange={(ev) => setReply(ev.target.value)} />
                    <button type="submit" disabled={!reply.trim()}>Send</button>
                  </form>
                  <details>
                    <summary className="small">Resolve this case</summary>
                    <label htmlFor="notes" className="sr-only">Private notes</label>
                    <textarea id="notes" rows={2} placeholder="Private notes, e.g. safety plan agreed (encrypted, never shown to the person)" value={notes} onChange={(ev) => setNotes(ev.target.value)} style={{ margin: "10px 0" }} />
                    <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                      <button onClick={() => resolve(true)}>Resolve and hand back to Arna</button>
                      <button className="quiet" onClick={() => resolve(false)}>Resolve and close the chat</button>
                    </div>
                  </details>
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </main>
  );
}
