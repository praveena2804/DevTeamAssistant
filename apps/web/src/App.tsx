import { Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";
import { NotFoundPage } from "./pages/NotFoundPage";
import { SystemStatusPage } from "./pages/SystemStatusPage";

/**
 * Application routes. Part A provides the shell and the system status page only. Product screens
 * (Spec §20.1) are added by the parts that own them (roadmap §14.3).
 */
export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<SystemStatusPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
