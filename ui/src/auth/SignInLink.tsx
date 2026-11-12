import type { ReactNode } from "react";
import { Link } from "react-router-dom";

export type SignInState = { signIn: true };

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
  const state: SignInState = { signIn: true };
  return (
    <Link to={`/login?${params}`} state={state} className={className}>
      {children}
    </Link>
  );
}
