import { Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";
import { ActivityPage } from "./pages/ActivityPage";
import { ExtractionReviewPage } from "./pages/ExtractionReviewPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { ProjectPage } from "./pages/ProjectPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { RunHistoryPage } from "./pages/RunHistoryPage";
import { RunPage } from "./pages/RunPage";
import { SandboxPage } from "./pages/SandboxPage";
import { SignInPage } from "./pages/SignInPage";
import { SystemStatusPage } from "./pages/SystemStatusPage";
import { TracePage } from "./pages/TracePage";

/**
 * Application routes. Part A: shell and system status. Part B: sign-in, projects dashboard,
 * create/upload, extraction review and activity (Spec §20.1 screens owned by Part B, roadmap §14.3).
 * Part D: build progress (live), gate decisions, trace viewer and run history.
 */
export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<SystemStatusPage />} />
        <Route path="signin" element={<SignInPage />} />
        <Route path="projects" element={<ProjectsPage />} />
        <Route path="projects/:projectId" element={<ProjectPage />} />
        <Route path="projects/:projectId/documents/:documentId" element={<ExtractionReviewPage />} />
        <Route path="projects/:projectId/activity" element={<ActivityPage />} />
        <Route path="projects/:projectId/runs/:runId" element={<RunPage />} />
        <Route path="projects/:projectId/runs/:runId/trace" element={<TracePage />} />
        <Route path="projects/:projectId/runs/:runId/history" element={<RunHistoryPage />} />
        <Route path="activity" element={<ActivityPage />} />
        <Route path="sandbox" element={<SandboxPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
