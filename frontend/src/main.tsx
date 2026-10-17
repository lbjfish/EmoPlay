import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { GameShell } from "./app/GameShell";
import "./styles/game.css";

const root = document.getElementById("root");
if (!root) {
  throw new Error("EmoPlay game root is unavailable.");
}

createRoot(root).render(
  <StrictMode>
    <GameShell />
  </StrictMode>,
);
