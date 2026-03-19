import { useMutation, useQueryClient } from "@tanstack/react-query"
import { showSuccess, showError } from "@/lib/toast"

interface OptimisticUpdate<TQueryData, TVariables> {
  queryKey: unknown[]
  updater: (oldData: TQueryData[], variables: TVariables) => TQueryData[]
}

interface UseApiMutationOptions<TData, TVariables, TQueryData> {
  mutationFn: (variables: TVariables) => Promise<TData>
  invalidateKeys?: unknown[][]
  onSuccess?: (data: TData, variables: TVariables) => void
  successMessage?: string
  errorMessage?: string
  optimisticUpdate?: OptimisticUpdate<TQueryData, TVariables>
}

export function useApiMutation<TData = unknown, TVariables = void, TQueryData = unknown>({
  mutationFn,
  invalidateKeys,
  onSuccess,
  successMessage,
  errorMessage,
  optimisticUpdate,
}: UseApiMutationOptions<TData, TVariables, TQueryData>) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn,
    onMutate: async (variables) => {
      if (!optimisticUpdate) return undefined

      const { queryKey, updater } = optimisticUpdate
      await queryClient.cancelQueries({ queryKey })
      const previousData = queryClient.getQueryData<TQueryData[]>(queryKey)

      if (previousData) {
        queryClient.setQueryData<TQueryData[]>(queryKey, updater(previousData, variables))
      }

      return { previousData, queryKey }
    },
    onError: (error: Error, _variables, context) => {
      if (context?.previousData !== undefined) {
        queryClient.setQueryData(context.queryKey, context.previousData)
      }
      showError(errorMessage ?? error.message ?? "An error occurred")
    },
    onSuccess: async (data, variables) => {
      if (invalidateKeys) {
        await Promise.all(
          invalidateKeys.map((key) =>
            queryClient.invalidateQueries({ queryKey: key })
          )
        )
      }
      if (successMessage) showSuccess(successMessage)
      onSuccess?.(data, variables)
    },
  })
}
