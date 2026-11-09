"use client"

import { useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { apiFetch } from "@/lib/api"
import { showSuccess } from "@/lib/toast"
import type { ActionPack, PackSourceType } from "@/lib/types"

export interface PackFormState {
  name: string
  source_type: PackSourceType
  git_repository_id: number | null
  path: string
  local_path: string
  enabled: boolean
  trusted: boolean
}

const emptyForm: PackFormState = {
  name: "",
  source_type: "git",
  git_repository_id: null,
  path: "",
  local_path: "",
  enabled: true,
  trusted: false,
}

function buildPayload(state: PackFormState): Record<string, unknown> {
  const p: Record<string, unknown> = {
    name: state.name,
    source_type: state.source_type,
    enabled: state.enabled,
    trusted: state.trusted,
  }
  if (state.source_type === "git") {
    p.git_repository_id = state.git_repository_id
    p.path = state.path ?? ""
  } else {
    p.local_path = state.local_path
  }
  return p
}

/**
 * The add/edit pack dialog's state. It lives with the page, not inside the
 * dialog, so a save still in flight when the dialog is closed and reopened
 * keeps showing (and finishing) as it always has.
 */
export function usePackForm() {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<ActionPack | null>(null)
  const [form, setForm] = useState<PackFormState | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [formSaving, setFormSaving] = useState(false)
  const queryClient = useQueryClient()

  function openCreate() {
    setEditing(null)
    setForm(emptyForm)
    setFormError(null)
    setDialogOpen(true)
  }

  function openEdit(pack: ActionPack) {
    setEditing(pack)
    setForm({
      name: pack.name,
      source_type: pack.source_type,
      git_repository_id: pack.git_repository_id,
      path: pack.path ?? "",
      local_path: pack.local_path ?? "",
      enabled: pack.enabled,
      trusted: pack.trusted,
    })
    setFormError(null)
    setDialogOpen(true)
  }

  async function handleSave() {
    if (!form) return
    setFormSaving(true)
    setFormError(null)
    try {
      if (editing) {
        await apiFetch(`/api/action-packs/${editing.id}`, {
          method: "PUT",
          json: buildPayload(form),
        })
        showSuccess("Action pack updated")
      } else {
        await apiFetch("/api/action-packs", {
          method: "POST",
          json: buildPayload(form),
        })
        showSuccess("Action pack created")
      }
      await queryClient.invalidateQueries({ queryKey: ["action-packs"] })
      await queryClient.invalidateQueries({ queryKey: ["actions-catalog"] })
      await queryClient.invalidateQueries({ queryKey: ["action-resolutions"] })
      setDialogOpen(false)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to save")
    } finally {
      setFormSaving(false)
    }
  }

  const setF = <K extends keyof PackFormState>(k: K, v: PackFormState[K]) => setForm((prev) => (prev ? { ...prev, [k]: v } : prev))
  const closeDialog = () => {
    setDialogOpen(false)
    setFormError(null)
  }

  return { dialogOpen, editing, form, formError, formSaving, openCreate, openEdit, handleSave, setF, closeDialog }
}
