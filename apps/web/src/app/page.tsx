import { ApiStatus } from "@/components/api-status";

export default function Home() {
  return (
    <div className="flex flex-1 flex-col bg-zinc-50 font-sans dark:bg-black">
      <header className="flex items-center justify-between border-b border-zinc-200 px-6 py-4 dark:border-zinc-800">
        <h1 className="text-sm font-semibold tracking-tight text-zinc-900 dark:text-zinc-100">
          Support Agent
        </h1>
        <ApiStatus />
      </header>
      <main className="flex flex-1 items-center justify-center p-6">
        <p className="text-sm text-zinc-500">Chat and voice interfaces arrive in Phases 4–5.</p>
      </main>
    </div>
  );
}
