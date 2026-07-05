// usePlan — the signed-in user's plan + monthly usage (from GET /me).
// Refreshes on mount and whenever `key` changes (pass a nav/route value to
// re-check after a generate). Silent on failure so it never blocks the UI.
import { useCallback, useEffect, useState } from "react";
import { getMe } from "../api.js";

export function usePlan(key) {
  const [plan, setPlan] = useState(null); // { auth, plan, used, limit }
  const refresh = useCallback(async () => {
    try {
      setPlan(await getMe());
    } catch {
      /* not signed in / backend open — leave null */
    }
  }, []);
  useEffect(() => {
    refresh();
  }, [refresh, key]);
  return { plan, refresh };
}
