/** Tiny History API router (no react-router dependency). */

import { createContext, useContext, useEffect, useMemo, useState } from "react";

const RouterContext = createContext({ path: "/", navigate: () => {} });

export function BrowserRouter({ children }) {
  const [path, setPath] = useState(() => window.location.pathname || "/");

  useEffect(() => {
    const onPop = () => setPath(window.location.pathname || "/");
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const navigate = (to, { replace = false } = {}) => {
    if (replace) window.history.replaceState({}, "", to);
    else window.history.pushState({}, "", to);
    setPath(to.split("?")[0] || "/");
  };

  const value = useMemo(() => ({ path, navigate }), [path]);
  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}

export function useRouter() {
  return useContext(RouterContext);
}

export function Link({ to, children, className, onClick, end = false }) {
  const { navigate, path } = useRouter();
  // Exact match when end=true so parent routes (e.g. Overview) don't stay
  // active on child pages like /import or /classification.
  const active = end ? path === to : path === to || path.startsWith(`${to}/`);
  return (
    <a
      href={to}
      className={[className, active ? "active" : ""].filter(Boolean).join(" ")}
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        onClick?.(e);
        navigate(to);
      }}
    >
      {children}
    </a>
  );
}

/** Match `/admin/foo/:id/bar/:draftId` style patterns. */
export function matchPath(pattern, path) {
  const pp = pattern.split("/").filter(Boolean);
  const sp = path.split("/").filter(Boolean);
  if (pp.length !== sp.length) return null;
  const params = {};
  for (let i = 0; i < pp.length; i++) {
    if (pp[i].startsWith(":")) params[pp[i].slice(1)] = decodeURIComponent(sp[i]);
    else if (pp[i] !== sp[i]) return null;
  }
  return params;
}
