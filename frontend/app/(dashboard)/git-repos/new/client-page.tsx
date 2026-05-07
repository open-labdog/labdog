"use client"

import { useReducer } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { Breadcrumb } from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import { AuthStep } from "@/components/git-repos/auth-step"
import { ScanStep } from "@/components/git-repos/scan-step"
import { ReviewStep } from "@/components/git-repos/review-step"
import { WizardStepIndicator, type WizardStep } from "@/components/git-repos/wizard-step-indicator"
import type { RepoScanResponse } from "@/lib/types"

type State = {
  step: WizardStep
  repoId: number | null
  repoName: string | null
  scanResult: RepoScanResponse | null
}

type Action =
  | { type: "REPO_CREATED"; repoId: number; repoName: string }
  | { type: "SCAN_SUCCESS"; result: RepoScanResponse }
  | { type: "BACK_TO_AUTH" }
  | { type: "BACK_TO_SCANNING" }

const initialState: State = {
  step: "auth",
  repoId: null,
  repoName: null,
  scanResult: null,
}

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "REPO_CREATED":
      return { ...state, step: "scanning", repoId: action.repoId, repoName: action.repoName }
    case "SCAN_SUCCESS":
      return { ...state, step: "review", scanResult: action.result }
    case "BACK_TO_AUTH":
      return initialState
    case "BACK_TO_SCANNING":
      return { ...state, step: "scanning", scanResult: null }
  }
}

export default function RepoOnboardingWizard() {
  const [state, dispatch] = useReducer(reducer, initialState)
  const router = useRouter()

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
      {state.step === "scanning" && state.repoId !== null && (
        <ScanStep
          repoId={state.repoId}
          repoName={state.repoName ?? ""}
          onScanned={(result) => dispatch({ type: "SCAN_SUCCESS", result })}
          onCancelled={() => dispatch({ type: "BACK_TO_AUTH" })}
        />
      )}
      {state.step === "review" && state.repoId !== null && state.scanResult !== null && (
        <ReviewStep
          repoId={state.repoId}
          scanResult={state.scanResult}
          onActivated={() => router.push(`/git-repos/${state.repoId}`)}
          onRescan={() => dispatch({ type: "BACK_TO_SCANNING" })}
        />
      )}
    </div>
  )
}
