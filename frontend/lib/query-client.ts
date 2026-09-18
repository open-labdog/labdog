import { QueryClient } from "@tanstack/react-query"

/**
 * The app's single QueryClient.
 *
 * Lives here rather than in `providers.tsx` so `lib/api.ts` can reach it
 * without importing a React component module: when a request comes back
 * 401 the cache has to be dropped before the redirect, or the next
 * session would briefly render the previous user's data (BUG-75).
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
  },
})
