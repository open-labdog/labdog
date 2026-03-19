import { toast } from 'sonner'

export const showSuccess = (message: string) => toast.success(message, { duration: 3000 })
export const showError = (message: string) => toast.error(message, { duration: Infinity })
export const showInfo = (message: string) => toast.info(message, { duration: 3000 })
