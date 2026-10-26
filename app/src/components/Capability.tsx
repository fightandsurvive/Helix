// app/src/components/Capability.tsx
// Renders children if enabled, otherwise a locked card with actionable guidance.
// Never shows a dead button — the lock state explains exactly what to do.

export const Capability = ({
  enabled,
  reason,
  title,
  children,
}: {
  enabled: boolean;
  reason: string;
  title: string;
  children: React.ReactNode;
}) => {
  if (enabled) return <>{children}</>;
  return (
    <div className="capability-locked">
      <div className="cap-lock">⊘</div>
      <div className="cap-title">{title} — Not Available</div>
      <div className="cap-reason">{reason}</div>
    </div>
  );
};
