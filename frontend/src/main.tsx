import React from "react";
import { createRoot } from "react-dom/client";
import WizardInfo from "./WizardInfo";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <WizardInfo />
  </React.StrictMode>
);
