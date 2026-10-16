import { toast, type ExternalToast } from 'sonner'

export const showSuccess = (message: string, options?: ExternalToast) => toast.success(message, { duration: 3000, ...options })
export const showError = (message: string, options?: ExternalToast) => toast.error(message, { duration: Infinity, ...options })
export const showInfo = (message: string, options?: ExternalToast) => toast.info(message, { duration: 3000, ...options })
