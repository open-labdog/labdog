"use client"

import { useReducer } from "react"
import Link from "next/link"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import { AuthStep } from "@/components/git-repos/auth-step"
import { WizardStepIndicator, type WizardStep } from "@/components/git-repos/wizard-step-indicator"

// Placeholder types for the wizard state. Replaced with the real
// schema mirrors in F4 (frontend/lib/types.ts).
type ScanResultPlaceholder = unknown
type SelectionsState = {
  packs: Record<string, { checked: boolean; role: "default" | "override" }>
  gitops: Record<string, { checked: boolean; host_group_id: number | null }>
}

type State = {
  step: WizardStep
  repoId: number | null
  repoName: string | null
  scanResult: ScanResultPlaceholder | null
  selections: SelectionsState
}

type Action =
  | { type: "REPO_CREATED"; repoId: number; repoName: string }
  | { type: "SCAN_SUCCESS"; result: ScanResultPlaceholder; selections: SelectionsState }
  | { type: "BACK_TO_AUTH" }
  | { type: "BACK_TO_SCANNING" }
  | { type: "UPDATE_SELECTIONS"; selections: SelectionsState }

const initialState: State = {
  step: "auth",
  repoId: null,
  repoName: null,
  scanResult: null,
  selections: { packs: {}, gitops: {} },
}

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "REPO_CREATED":
      return { ...state, step: "scanning", repoId: action.repoId, repoName: action.repoName }
    case "SCAN_SUCCESS":
      return { ...state, step: "review", scanResult: action.result, selections: action.selections }
    case "BACK_TO_AUTH":
      return initialState
    case "BACK_TO_SCANNING":
      return { ...state, step: "scanning", scanResult: null }
    case "UPDATE_SELECTIONS":
      return { ...state, selections: action.selections }
  }
}

function ScanStepStub({ repoName }: { repoName: string | null }) {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900 p-6">
      <p className="text-sm text-slate-400">
        Scanning <span className="text-slate-200">{repoName ?? "repository"}</span>… (wired in F3)
      </p>
    </div>
  )
}

function ReviewStepStub() {
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900 p-6">
      <p className="text-sm text-slate-400">Review and activate UI goes here (wired in F4).</p>
    </div>
  )
}

export default function RepoOnboardingWizard() {
  const [state, dispatch] = useReducer(reducer, initialState)

  return (
    <div className="max-w-3xl space-y-6">
      <Breadcrumb
        items={[{ label: "Git Repos", href: "/git-repos" }, { label: "Connect repository" }]}
      />
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white">Connect a git repository</h1>
          <p className="text-slate-400 text-sm mt-1">
            Add a repository, scan it for action packs and GitOps configs, and activate the ones you
            want LabDog to manage.
          </p>
        </div>
        <Link href="/git-repos">
          <Button type="button" variant="outline">
            Cancel
          </Button>
        </Link>
      </div>

      <WizardStepIndicator current={state.step} />

      {state.step === "auth" && (
        <AuthStep
          onCreated={(repo) =>
            dispatch({ type: "REPO_CREATED", repoId: repo.id, repoName: repo.name })
          }
        />
      )}
      {state.step === "scanning" && <ScanStepStub repoName={state.repoName} />}
      {state.step === "review" && <ReviewStepStub />}
    </div>
  )
}
