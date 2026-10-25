import { TryDemo } from "./actions";
import { noSignIn, useProviders } from "./providers";

// One button per sign in provider the server offers, so adding a provider needs no web change. A server with none,
// or one that cannot say, offers the demo instead of a dead end.
export default function SignInButtons({ next, className }: { next?: string; className: string }) {
  const providers = useProviders();
  if (noSignIn(providers)) {
    return (
      <div className="login-none">
        <p role="status">
          {providers === "failed"
            ? "Sign in is not available right now. You can try the demo meanwhile."
            : "Sign in is not set up on this server yet. You can try the demo meanwhile."}
        </p>
        <TryDemo className={className} />
      </div>
    );
  }
  if (!Array.isArray(providers)) return <p role="status">Loading sign in options</p>;
  const query = next ? `?next=${encodeURIComponent(next)}` : "";
  return (
    <div className="login-buttons">
      {providers.map((p) => (
        <a key={p.name} className={className} href={`/auth/${encodeURIComponent(p.name)}/start${query}`}>
          Continue with {p.label}
        </a>
      ))}
    </div>
  );
}
