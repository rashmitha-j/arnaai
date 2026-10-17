import Link from "next/link";

export function TopBar({ children }: { children?: React.ReactNode }) {
  return (
    <header className="topbar">
      <Link href="/" className="brand">
        <span className="brand-dot" aria-hidden />
        ArnaAI
      </Link>
      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>{children}</div>
    </header>
  );
}
