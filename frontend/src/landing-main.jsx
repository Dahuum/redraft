import { createRoot } from "react-dom/client";
import { RdButton } from "./components/landing/RdButton.jsx";
import "./landing-heroui.css";

function mount(id, node) {
  const el = document.getElementById(id);
  if (!el) return;
  createRoot(el).render(node);
}

mount(
  "nav-signin-root",
  <RdButton className="nav__signin" dataModal="signin">
    Sign In
  </RdButton>
);

mount(
  "nav-getstarted-root",
  <RdButton className="btn btn--primary btn--pill" dataModal="signup">
    Get Started
  </RdButton>
);

mount(
  "hero-primary-cta-root",
  <RdButton className="btn btn--primary btn--lg" dataModal="signup">
    Start Editing Free
  </RdButton>
);

mount(
  "hero-secondary-cta-root",
  <RdButton as="link" href="#product" className="btn btn--ghost btn--lg">
    See how it works
    <svg width="14" height="14" viewBox="0 0 13.333 13.333" fill="currentColor" aria-hidden="true">
      <path d="M 10.146 7.5 L 0 7.5 L 0 5.833 L 10.146 5.833 L 5.479 1.167 L 6.667 0 L 13.333 6.667 L 6.667 13.333 L 5.479 12.167 L 10.146 7.5 L 10.146 7.5" />
    </svg>
  </RdButton>
);

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
