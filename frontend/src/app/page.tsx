import Link from "next/link";
import { HeroOrb } from "@/components/HeroOrb";
import { TopBar } from "@/components/TopBar";

export default function Home() {
  return (
    <main className="wrap">
      <TopBar>
        <Link href="/counselor" className="small muted">For counselors</Link>
      </TopBar>

      <section className="hero">
        <div>
          <h1>Talk it through, at your own pace.</h1>
          <p className="lead">
            Arna listens, notices how you&apos;re feeling, and works through difficult thoughts with you
            using CBT. When things get heavy, a real person joins in.
          </p>
          <div className="actions">
            <Link href="/chat" className="btn">Start talking</Link>
          </div>
        </div>
        <HeroOrb />
      </section>

      <section className="how" aria-label="How Arna works">
        <div>
          <h2>Notices how you feel</h2>
          <p>Arna reads the feeling in your words and, if you turn on your camera, your expression too. The camera is analysed on your device and the video is never uploaded.</p>
        </div>
        <div>
          <h2>Works through thoughts with you</h2>
          <p>Step by step, you look at one situation, the thought behind the feeling, and a more balanced way to see it: the thought record used in cognitive behavioural therapy.</p>
        </div>
        <div>
          <h2>Brings in a person when it matters</h2>
          <p>If you mention being unsafe, Arna pauses, shares crisis lines, and a trained counselor joins the same conversation.</p>
        </div>
      </section>

      <p className="footnote">
        Arna is an AI companion, not a therapist or a medical service. If you are in danger right now,
        call 112 (India) or your local emergency number. In India you can call Tele-MANAS on 14416, any time, for free.
      </p>
    </main>
  );
}
