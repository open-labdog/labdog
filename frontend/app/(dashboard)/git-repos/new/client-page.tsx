"use client"

import { useReducer } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { PageHead, Steps } from "@/components/ld"
import { AuthStep } from "@/components/git-repos/auth-step"
import { ScanStep } from "@/components/git-repos/scan-step"
import { ReviewStep } from "@/components/git-repos/review-step"
import type { RepoScanResponse } from "@/lib/types"

export type WizardStep = "auth" | "scanning" | "review"

const STEPS: { k: WizardStep; label: string }[] = [
  { k: "auth", label: "connect" },
  { k: "scanning", label: "scan" },
  { k: "review", label: "review & activate" },
]

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

/**
 * Connecting a repository is three steps that each leave the system in
 * a known state: connect (the repository row exists), scan (what it
 * holds), review & activate (which packs and GitOps files LabDog takes).
 */
export default function RepoOnboardingWizard() {
  const [state, dispatch] = useReducer(reducer, initialState)
  const router = useRouter()

  return (
    <>
      <PageHead
        crumbs={[
          { label: "settings", href: "/settings" },
          { label: "integrations", href: "/settings" },
          { label: "git repositories", href: "/git-repos" },
        ]}
        title="Connect a git repository"
        sub="Add a repository, scan it for action packs and GitOps files, and activate the ones LabDog should manage."
        actions={
          <Link href="/git-repos" className="btn btn-sm hover:no-underline">
            Cancel
          </Link>
        }
      >
        <Steps steps={STEPS} current={state.step} />
      </PageHead>

      <div className="scroll flex flex-1 flex-col gap-3 p-3.5" style={{ maxWidth: 860 }}>
        {state.step === "auth" && <AuthStep onCreated={(repo) => dispatch({ type: "REPO_CREATED", repoId: repo.id, repoName: repo.name })} />}
        {state.step === "scanning" && state.repoId !== null && (
          <ScanStep repoId={state.repoId} repoName={state.repoName ?? ""} onScanned={(result) => dispatch({ type: "SCAN_SUCCESS", result })} onCancelled={() => dispatch({ type: "BACK_TO_AUTH" })} />
        )}
        {state.step === "review" && state.repoId !== null && state.scanResult !== null && (
          <ReviewStep repoId={state.repoId} scanResult={state.scanResult} onActivated={() => router.push(`/git-repos/${state.repoId}`)} onRescan={() => dispatch({ type: "BACK_TO_SCANNING" })} />
        )}
      </div>
    </>
  )
}
