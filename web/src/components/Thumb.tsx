import { useState } from "react";

type Props = { src: string | null; alt: string; w?: number; h?: number; label?: string; fill?: boolean };

export function Thumb({ src, alt, w = 96, h = 72, label, fill }: Props) {
  const [broken, setBroken] = useState<string | null>(null);
  return (
    <div className={`thumb${fill ? " thumb-fill" : ""}`} style={fill ? undefined : { width: w, height: h }}>
      {src && broken !== src ? (
        <img src={src} alt={alt} loading="lazy" onError={() => setBroken(src)} />
      ) : (
        <span className="xsmall">{label ?? "No picture"}</span>
      )}
    </div>
  );
}
