import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import PresentView from "./views/PresentView";
import "./index.css";

// ?present=1 is the second-screen presenter window (see lib/present.ts).
const presenting = new URLSearchParams(location.search).has("present");

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {presenting ? <PresentView /> : <App />}
  </React.StrictMode>,
);
