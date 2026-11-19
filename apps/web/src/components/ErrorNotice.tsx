import { Link } from "react-router-dom";

import { ApiError } from "../api/client";

/** Plain-language error display. A 401 becomes a sign-in prompt. */
export function ErrorNotice({ error }: { error: Error }) {
  if (error instanceof ApiError && error.status === 401) {
    return (
      <div role="alert" className="notice">
        <p>
          You are not signed in. Open the sign-in link printed by <code>scripts/dev.ps1</code>, or{" "}
          <Link to="/signin">sign in with your key</Link>.
        </p>
      </div>
    );
  }
  return (
    <div role="alert" className="notice notice-error">
      <p>{error.message}</p>
    </div>
  );
}
