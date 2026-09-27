import ReactDOM from "react-dom/client";
import App from "./App";
import { startSession } from "./lib/session";
import "./styles/editor.css";

// NOTE: React.StrictMode is intentionally omitted. React Flow v12 measures each
// node's handle bounds via a per-node ResizeObserver mounted in an effect;
// StrictMode's deliberate double-mount in dev tears that observer down and
// leaves `nodesInitialized: false`, so edges never get endpoints and fitView
// never fires. StrictMode is a dev-only aid (stripped from production builds),
// so dropping it changes nothing shipped while fixing the dev render.
startSession(() => ReactDOM.createRoot(document.getElementById("root")!).render(<App />));
