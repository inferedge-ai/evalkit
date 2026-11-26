import { extensions } from "../extensions";

export function appHome(): string {
  return extensions.landingPage ? "/home" : "/";
}
