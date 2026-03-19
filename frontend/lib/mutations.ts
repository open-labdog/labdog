import { useMutation, useQueryClient } from "@tanstack/react-query"
import { showSuccess, showError } from "@/lib/toast"

interface UseApiMutationOptions<TData, TVariables> {
  mutationFn: (variables: TVariables) => Promise<TData>
  invalidateKeys?: unknown[][]
  onSuccess?: (data: TData, variables: TVariables) => void
  successMessage?: string
  errorMessage?: string
}

export function useApiMutation<TData = unknown, TVariables = void>({
  mutationFn,
  invalidateKeys,
  onSuccess,
  successMessage,
  errorMessage,
}: UseApiMutationOptions<TData, TVariables>) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn,
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
    onError: (error: Error) => {
      showError(errorMessage ?? error.message ?? "An error occurred")
    },
  })
}
