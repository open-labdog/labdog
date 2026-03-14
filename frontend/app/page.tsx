export default function Home() {
  return (
    <div className="flex flex-col items-start justify-start">
      <div className="mb-12">
        <h1 className="text-4xl font-bold text-white mb-2">Barricade</h1>
        <p className="text-xl text-slate-400">Firewall Management</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 w-full max-w-4xl">
        <div className="rounded-lg border border-slate-700 bg-slate-900 p-6">
          <h2 className="text-lg font-semibold text-white mb-2">Dashboard</h2>
          <p className="text-slate-400">
            Monitor and manage your firewall rules across all hosts.
          </p>
        </div>

        <div className="rounded-lg border border-slate-700 bg-slate-900 p-6">
          <h2 className="text-lg font-semibold text-white mb-2">Groups</h2>
          <p className="text-slate-400">
            Organize hosts into logical groups for easier management.
          </p>
        </div>

        <div className="rounded-lg border border-slate-700 bg-slate-900 p-6">
          <h2 className="text-lg font-semibold text-white mb-2">Hosts</h2>
          <p className="text-slate-400">
            View and configure firewall rules for individual hosts.
          </p>
        </div>

        <div className="rounded-lg border border-slate-700 bg-slate-900 p-6">
          <h2 className="text-lg font-semibold text-white mb-2">SSH Keys</h2>
          <p className="text-slate-400">
            Manage SSH keys for secure host access and automation.
          </p>
        </div>
      </div>

      <div className="mt-12 text-sm text-slate-500">
        <p>Ansible-based Linux firewall manager</p>
      </div>
    </div>
  )
}
