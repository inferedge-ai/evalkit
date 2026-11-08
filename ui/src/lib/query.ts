import {
  QueryClient,
  useQuery,
  type UseQueryOptions,
  type UseQueryResult,
} from "@tanstack/react-query";
import { api, ApiError } from "./api";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: false,
      // A 4xx answer won't change on retry; a 5xx or a dropped connection may.
      retry: (failures, error) =>
        !(error instanceof ApiError && error.status < 500) && failures < 2,
    },
  },
});

type ApiQueryOptions<T> = Omit<
  UseQueryOptions<T>,
  "queryKey" | "queryFn" | "enabled"
>;

// One query per API path, so components that ask for the same path share it.
// A null path waits until the caller knows what to ask for.
export function useApi<T>(
  path: string | null,
  options?: ApiQueryOptions<T>,
): UseQueryResult<T> {
  return useQuery<T>({
    queryKey: [path],
    queryFn: () => api<T>(path as string),
    enabled: path !== null,
    ...options,
  });
}

export function getApiData<T>(path: string): T | undefined {
  return queryClient.getQueryData<T>([path]);
}

// For a write whose response is the new state of what `path` returns.
export function setApiData<T>(path: string, data: T): void {
  queryClient.setQueryData([path], data);
}

// After a write: refetch every query on screen and mark the rest stale, so
// each page that shows the changed data is current without a reload.
export function refreshQueries(): Promise<void> {
  return queryClient.invalidateQueries();
}
