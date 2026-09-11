/** The sign-in screen, and the only thing an anonymous visitor can reach. */

import { startGoogleLogin } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Notice } from "../components/bits";
import googleMark from "../assets/google.svg";

export function Gate() {
  const { status, error } = useAuth();

  return (
    <div className="gate">
      <div className="gate__card">
        <span className="mark">
          <span className="mark__dot" />
          Microservices
        </span>

        <p className="gate__note">
          Companiile din Republica Moldova și situațiile lor financiare.
        </p>

        {status === "error" && error && <Notice>{error}</Notice>}

        <button className="btn" type="button" onClick={startGoogleLogin}>
          <img src={googleMark} alt="" width={16} height={16} />
          continuă cu Google
        </button>
      </div>
    </div>
  );
}
