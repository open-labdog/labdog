import GenericActionRunPage from "./client-page"

export async function generateStaticParams() {
  return [{ runId: "placeholder" }]
}

export default function Page() {
  return <GenericActionRunPage />
}
