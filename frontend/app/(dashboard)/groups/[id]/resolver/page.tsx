import ClientPage from "./client-page"

export async function generateStaticParams() {
  return [{ id: "placeholder" }]
}

export default function Page() {
  return <ClientPage />
}
