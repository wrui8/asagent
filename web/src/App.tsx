import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { lastSession } from "./lib/nav";
import { Admin } from "./pages/Admin";
import { Audit } from "./pages/Audit";
import { Chat } from "./pages/Chat";
import { Evals } from "./pages/Evals";
import { KB } from "./pages/KB";
import { Login } from "./pages/Login";
import { NewSession } from "./pages/NewSession";
import { Sessions } from "./pages/Sessions";
import { System } from "./pages/System";

function ChatRedirect() {
  const sessionId = lastSession();
  return <Navigate to={sessionId ? `/sessions/${sessionId}` : "/"} replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<Layout />}>
        <Route index element={<Sessions />} />
        <Route path="new" element={<NewSession />} />
        <Route path="chat" element={<ChatRedirect />} />
        <Route path="sessions/:id" element={<Chat />} />
        <Route path="kb" element={<KB />} />
        <Route path="evals" element={<Evals />} />
        <Route path="audit" element={<Audit />} />
        <Route path="system" element={<System />} />
        <Route path="admin" element={<Admin />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
