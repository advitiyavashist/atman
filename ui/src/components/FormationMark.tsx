/** Formation-dots mark: abstract seats / constellation. No edges, no pitch. */
export function FormationMark({ size = 22 }: { size?: number }) {
  return (
    <svg
      className="mark"
      viewBox="0 0 32 32"
      width={size}
      height={size}
      role="img"
      aria-label="atman"
    >
      <circle cx="10" cy="7.8" r="3.35" fill="currentColor" />
      <circle cx="22.4" cy="8.8" r="3.35" fill="currentColor" />
      <circle cx="6.6" cy="17.6" r="3.35" fill="currentColor" />
      <circle cx="25.4" cy="18.2" r="3.35" fill="currentColor" />
      <circle cx="16" cy="24.6" r="3.35" fill="currentColor" />
    </svg>
  );
}
