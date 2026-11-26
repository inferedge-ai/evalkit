import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { appHome } from "../lib/home";

export type SignInState = { signIn: true };

export function SignInLink({
  next = appHome(),
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
