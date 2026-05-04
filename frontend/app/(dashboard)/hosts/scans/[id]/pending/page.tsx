import ScanPendingRedirectClient from "./redirect-client"

export async function generateStaticParams() {
  return [{ id: "placeholder" }]
}

export default function Page() {
  return <ScanPendingRedirectClient />
}
