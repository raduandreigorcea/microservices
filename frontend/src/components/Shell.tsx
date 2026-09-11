/** The frame every signed-in screen sits in. */

import { Link, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "../lib/auth";
import { Spinner } from "./bits";

const LINKS = [
  { to: "/", label: "panou", end: true },
  { to: "/companies", label: "companii", end: false },
  { to: "/jobs", label: "joburi", end: false },
  { to: "/scrape", label: "scrape", end: false },
];

/** Exact match for the panel, prefix match for the sections under it. */
function isCurrent(pathname: string, link: { to: string; end: boolean }): boolean {
  return link.end
    ? pathname === link.to
    : pathname === link.to || pathname.startsWith(`${link.to}/`);
}

export function Shell() {
  const { user, signOut, signingOut } = useAuth();
  const { pathname } = useLocation();

  return (
    <div className="shell">
      <header className="masthead">
        <div className="wrap">
          <div className="masthead__inner">
            <Link to="/" className="mark">
              <span className="mark__dot" />
              Microservices
            </Link>

            <nav className="nav">
              {LINKS.map((link) => (
                <Link
                  key={link.to}
                  to={link.to}
                  viewTransition
                  className={`nav__link${isCurrent(pathname, link) ? " is-active" : ""}`}
                >
                  {link.label}
                </Link>
              ))}
            </nav>

            <div className="whoami">
              {user?.picture_url && (
                <img
                  className="whoami__face"
                  src={user.picture_url}
                  alt=""
                  width={28}
                  height={28}
                  referrerPolicy="no-referrer"
                />
              )}
              <span className="whoami__mail" title={user?.email}>
                {user?.email}
              </span>
              <button
                className={`btn${signingOut ? " is-busy" : ""}`}
                type="button"
                onClick={() => void signOut()}
                disabled={signingOut}
              >
                {signingOut && <Spinner />}
                {signingOut ? "ies…" : "ieși"}
              </button>
            </div>
          </div>
        </div>
      </header>

      <main className="wrap ruled page">
        <Outlet />
      </main>

      <footer className="wrap">
        <div className="footer">
          <span>Microservices · date publice din Republica Moldova</span>
        </div>
      </footer>
    </div>
  );
}
