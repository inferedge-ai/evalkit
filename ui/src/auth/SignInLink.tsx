import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { clearSignOut } from "../lib/api";

export function SignInLink({
  next = "/",
  prompt,
  className,
  children,
}: {
  next?: string;
  prompt?: string;
  className?: string;
  children: ReactNode;
}) {
  const params = new URLSearchParams({ next });
  if (prompt) params.set("prompt", prompt);
  return (
    <Link
      to={`/login?${params}`}
      className={className}
      onClick={() => clearSignOut()}
    >
      {children}
    </Link>
  );
}
