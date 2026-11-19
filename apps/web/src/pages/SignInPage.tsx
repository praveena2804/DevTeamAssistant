import { type FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ApiError, signIn } from "../api/client";

function keyFromFragment(): string | null {
  const match = /(?:^#|&)token=([^&]+)/.exec(window.location.hash);
  return match?.[1] ? decodeURIComponent(match[1]) : null;
}

function describe(err: unknown): string {
  if (err instanceof ApiError && err.status === 401) {
    return "That key is not valid. Copy the sign-in link again from the terminal running scripts/dev.ps1.";
  }
  return err instanceof Error ? err.message : "Sign-in failed.";
}

/**
 * Sign-in (D-29 (a)). `scripts/dev.ps1` opens `/signin#token=…`. The token is in the URL fragment, which
 * the browser never sends to a server. It is posted once, exchanged for an HttpOnly cookie, and removed
 * from the address bar immediately.
 */
export function SignInPage() {
  const navigate = useNavigate();
  const [linkKey] = useState(keyFromFragment);
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(linkKey !== null);

  useEffect(() => {
    if (linkKey === null) return;
    window.history.replaceState(null, "", window.location.pathname); // drop the key from the URL
    let active = true;
    signIn(linkKey)
      .then(() => navigate("/projects", { replace: true }))
      .catch((err: unknown) => {
        if (!active) return;
        setError(describe(err));
        setBusy(false);
      });
    return () => {
      active = false;
    };
  }, [linkKey, navigate]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!token.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await signIn(token.trim());
      navigate("/projects", { replace: true });
    } catch (err) {
      setError(describe(err));
      setBusy(false);
    }
  }

  return (
    <section>
      <h1>Sign in</h1>
      <p>DevTeam AI only accepts requests from this computer, signed in with its local key.</p>
      {busy && <p role="status">Signing in…</p>}
      {error && (
        <p role="alert" className="notice notice-error">
          {error}
        </p>
      )}
      <form onSubmit={(e) => void onSubmit(e)}>
        <label htmlFor="token">Sign-in key</label>
        <input
          id="token"
          type="password"
          autoComplete="off"
          value={token}
          onChange={(e) => setToken(e.target.value)}
        />
        <button type="submit" disabled={busy}>
          Sign in
        </button>
      </form>
    </section>
  );
}
