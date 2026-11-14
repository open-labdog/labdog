/**
 * The seam Enterprise pages plug into. Community ships it empty.
 *
 * The Enterprise frontend build copies this frontend, adds its own files
 * and replaces this one module with a copy that fills the lists below
 * (`unbork-enterprise/frontend/lib/extensions.ts`). Nothing else in
 * Community changes, and with empty lists every page looks as it always did.
 *
 * Change these types only additively: an Enterprise build made for an
 * older Community still has to type-check against them.
 */
import type { ComponentType } from "react"
import type { AdminUser } from "@/lib/types"

/** One more tab under Settings, shown after Community's own. */
export interface SettingsSectionExtension {
  /** The `?section=` value. Must not clash with Community's sections. */
  key: string
  label: string
  /** Hidden from users who lack it. The API enforces it either way. */
  permission?: string
  Component: ComponentType
}

/** A panel in the Users page's edit dialog, under the role picker. */
export interface UserPanelExtension {
  key: string
  permission?: string
  Component: ComponentType<{ user: AdminUser }>
}

export const settingsSections: SettingsSectionExtension[] = []
export const userPanels: UserPanelExtension[] = []
