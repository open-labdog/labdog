export function AuthBackground({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="relative min-h-[100dvh] flex items-center justify-center overflow-hidden bg-slate-950"
      style={{
        backgroundImage:
          "radial-gradient(circle, oklch(0.28 0 0) 1px, transparent 1px)",
        backgroundSize: "24px 24px",
      }}
    >
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 h-[500px]"
        style={{
          backgroundImage:
            "radial-gradient(ellipse 600px 400px at 50% 0%, oklch(0.35 0.08 264 / 0.5) 0%, transparent 70%)",
        }}
      />
      <div className="relative w-full max-w-md px-4 py-8">{children}</div>
    </div>
  )
}
