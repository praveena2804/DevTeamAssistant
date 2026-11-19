import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <section>
      <h1>Page not found</h1>
      <p>
        This page does not exist. <Link to="/">Return to system status</Link>.
      </p>
    </section>
  );
}
