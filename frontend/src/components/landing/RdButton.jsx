import { Button, Link } from "@heroui/react";

/**
 * Thin wrapper so landing-main.jsx can mount HeroUI's Button/Link with the
 * page's existing .btn* classes (visual look stays exactly as-is) while
 * gaining HeroUI's press/hover/focus interaction layer. `dataModal` maps to
 * a plain `data-modal` DOM attribute so the untouched vanilla wiring in
 * public/modal.js keeps finding and handling these buttons exactly as it
 * does for static ones.
 */
export function RdButton({ as = "button", className, dataModal, children, ...rest }) {
  const Component = as === "link" ? Link : Button;
  return (
    <Component className={className} data-modal={dataModal} {...rest}>
      {children}
    </Component>
  );
}
