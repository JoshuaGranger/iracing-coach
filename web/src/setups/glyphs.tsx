/** Small shapes of the design language: the checkered flag marks a raced setup, the chevron opens a row. */
export function CheckeredFlag({ size = 16 }: { size?: number }) {
  const cells: [number, number][] = [];
  for (let y = 0; y < 3; y++) for (let x = 0; x < 4; x++) if ((x + y) % 2 === 0) cells.push([x, y]);
  return (
    <svg class="glyph flag" width={size} height={(size * 3) / 4 + 4} viewBox="0 0 16 15" aria-hidden="true" focusable="false">
      <path d="M2 14.5V1" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" />
      {cells.map(([x, y]) => (
        <rect key={`${x}${y}`} x={3.5 + x * 3 } y={1 + y * 3} width="3" height="3" fill="currentColor" />
      ))}
      <rect x="3.5" y="1" width="12" height="9" fill="none" stroke="currentColor" stroke-width="0.8" />
    </svg>
  );
}

export function Chevron({ down = false }: { down?: boolean }) {
  return (
    <svg class="glyph chevron" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false" style={down ? "transform:rotate(90deg)" : undefined}>
      <path d="M6 3.5l4.5 4.5L6 12.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
    </svg>
  );
}
