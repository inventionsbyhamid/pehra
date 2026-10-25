import type { ReactNode } from "react";

// A plain table that scrolls inside its card on a narrow screen instead of pushing the page sideways.
export function Table({ caption, head, children }: { caption: string; head: ReactNode[]; children: ReactNode }) {
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label={caption}>
      <table className="table">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>{head.map((h, i) => <th key={i} scope="col">{h}</th>)}</tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
