// Backend-for-frontend proxy: the browser only ever talks to this origin, and
// THERAPY_API_URL is read at runtime (works on Cloud Run without a rebuild).
import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
// A sleeping free-tier backend (e.g. Render) can take about a minute to wake up.
export const maxDuration = 120;

const API = (process.env.THERAPY_API_URL || "http://127.0.0.1:8080").replace(/\/$/, "");
const FORWARD = ["authorization", "content-type", "x-counselor-name"];

async function proxy(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const url = `${API}/api/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const headers = new Headers();
  for (const h of FORWARD) {
    const v = req.headers.get(h);
    if (v) headers.set(h, v);
  }
  const ip = req.headers.get("x-forwarded-for");
  if (ip) headers.set("x-forwarded-for", ip);

  try {
    const res = await fetch(url, {
      method: req.method,
      headers,
      body: ["GET", "HEAD"].includes(req.method) ? undefined : await req.text(),
      cache: "no-store",
    });
    return new Response(await res.text(), {
      status: res.status,
      headers: { "content-type": res.headers.get("content-type") || "application/json", "cache-control": "no-store" },
    });
  } catch {
    return Response.json({ error: "The Arna service can't be reached. Check that the therapy service is running." }, { status: 502 });
  }
}

export { proxy as GET, proxy as POST, proxy as DELETE };
