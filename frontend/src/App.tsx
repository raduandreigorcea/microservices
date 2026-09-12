import { Navigate, Route, Routes } from "react-router-dom";

import { Shell } from "./components/Shell";
import { useAuth } from "./lib/auth";
import { Companies } from "./routes/Companies";
import { Company } from "./routes/Company";
import { Gate } from "./routes/Gate";
import { Job } from "./routes/Job";
import { Jobs } from "./routes/Jobs";
import { NewScrape } from "./routes/NewScrape";
import { Overview } from "./routes/Overview";
import { Statement } from "./routes/Statement";
import { Empty } from "./components/bits";

export function App() {
  const { status } = useAuth();

  if (status === "loading") return <Booting />;
  if (status !== "signed-in") return <Gate />;

  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Overview />} />
        <Route path="companies" element={<Companies />} />
        <Route path="companies/:idno" element={<Company />} />
        <Route path="companies/:idno/statements/:year" element={<Statement />} />
        <Route path="jobs" element={<Jobs />} />
        <Route path="jobs/:id" element={<Job />} />
        <Route path="scrape" element={<NewScrape />} />
        <Route
          path="404"
          element={<Empty title="Pagina asta nu există." note="Verifică adresa." />}
        />
        <Route path="*" element={<Navigate to="/404" replace />} />
      </Route>
    </Routes>
  );
}

/** Shown for the moment before we know who is here. Same screen as the gate,
 *  so it carries the same wordmark. */
function Booting() {
  return (
    <div className="gate">
      <div className="gate__card">
        <span className="mark">
          <span className="mark__dot" />
          Microservices
        </span>
      </div>
    </div>
  );
}
