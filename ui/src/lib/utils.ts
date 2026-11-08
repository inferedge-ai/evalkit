import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

// theme.css adds font sizes (text-meta, text-body, ...) that tailwind-merge
// would otherwise read as colours and drop next to a text colour.
const twMerge = extendTailwindMerge({
  extend: {
    theme: { text: ["micro", "meta", "body", "lede", "head", "title"] },
  },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
