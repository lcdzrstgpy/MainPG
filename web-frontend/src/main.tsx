import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import "./shared/styles/global.css";
import "./shared/styles/themes.css";
import "./shared/styles/framework-flow.css";
import "./modules/product_processing/styles/product-processing.css";
import "./shared/styles/apple-workspace.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
