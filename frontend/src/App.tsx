import { useState } from "react";
import { Shell } from "./layouts/Shell";
import { SessionsPage } from "./pages/SessionsPage";
import { SessionPage } from "./pages/SessionPage";

export default function App() {
  const [activeSession, setActiveSession] = useState<string | null>(null);

  return (
    <Shell>
      {activeSession ? (
        <SessionPage
          sessionId={activeSession}
          onBack={() => setActiveSession(null)}
        />
      ) : (
        <SessionsPage onOpen={(id) => setActiveSession(id)} />
      )}
    </Shell>
  );
}
