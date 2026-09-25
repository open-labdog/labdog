/**
 * Compatibility shim: the confirmation modal lives in the kit now
 * (`components/ld/confirm.tsx`). Callers that still import from here get
 * the kit modal under the old name; new code imports `Confirm` from
 * `@/components/ld`. Goes when the last legacy caller is converted.
 */
export { Confirm as ConfirmDialog } from "@/components/ld/confirm"
