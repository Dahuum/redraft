import { createRoot } from "react-dom/client";
import { RdButton } from "./components/landing/RdButton.jsx";
import "./landing-heroui.css";

function mount(id, node) {
  const el = document.getElementById(id);
  if (!el) return;
  createRoot(el).render(node);
}

mount(
  "pricing-free-cta-root",
  <RdButton className="btn btn--ghost btn--block" dataModal="signup">
    Get Started Free
  </RdButton>
);

mount(
  "pricing-pro-cta-root",
  <RdButton className="btn btn--primary btn--block" dataModal="signup">
    Get Pro free
  </RdButton>
);
