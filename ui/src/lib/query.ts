import {
  QueryClient,
  skipToken,
  useQuery,
  type UseQueryOptions,
  type UseQueryResult,
} from "@tanstack/react-query";
import { api } from "./api";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: false,
      // Many reads reach a client's harness through the server; a retry would
      // put more load on a harness that is already failing.
      retry: false,
    },
  },
});

type ApiQueryOptions<T> = Omit<UseQueryOptions<T>, "queryKey" | "queryFn">;

// One query per API path, so components that ask for the same path share it.
// A null path waits until the caller knows what to ask for.
export function useApi<T>(
  path: string | null,
  options?: ApiQueryOptions<T>,
): UseQueryResult<T> {
  return useQuery<T>({
    queryKey: [path],
    queryFn: path === null ? skipToken : () => api<T>(path),
    ...options,
  });
}

export function getApiData<T>(path: string): T | undefined {
  return queryClient.getQueryData<T>([path]);
}

// For a write whose response is the new state of what `path` returns. A
// refetch already in flight is cancelled so it can't land on top of it.
export async function setApiData<T>(path: string, data: T): Promise<void> {
  await queryClient.cancelQueries({ queryKey: [path] });
  queryClient.setQueryData([path], data);
}

// After a write: refetch every query on screen and mark the rest stale, so
// each page that shows the changed data is current without a reload.
export function refreshQueries(): Promise<void> {
  return queryClient.invalidateQueries();
}
