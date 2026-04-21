import HostActionRunPage from "./client-page"

export async function generateStaticParams() {
  return [{ id: "placeholder", runId: "placeholder" }]
}

export default function Page() {
  return <HostActionRunPage />
}
