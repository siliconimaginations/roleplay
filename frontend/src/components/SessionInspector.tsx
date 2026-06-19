import type { SessionDetail } from "../api/types";

interface Props {
  session: SessionDetail;
}

export function SessionInspector({ session }: Props) {
  const allParties = [
    ...session.parties,
    ...(session.environment ? [session.environment] : []),
  ];

  return (
    <div className="h-full overflow-auto px-4 py-4 space-y-6">
      {/* Config */}
      <section>
        <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
          Config
        </h3>
        <div className="rounded-lg bg-gray-900 border border-gray-800 p-3">
          <dl className="space-y-1 text-sm">
            {Object.entries(session.config)
              .filter(([k]) => k !== "parties" && k !== "environment")
              .map(([k, v]) => (
                <div key={k} className="flex gap-2">
                  <dt className="text-gray-500 shrink-0 w-36 font-mono text-xs">
                    {k}
                  </dt>
                  <dd className="text-gray-200 font-mono text-xs break-all">
                    {typeof v === "object"
                      ? JSON.stringify(v)
                      : String(v ?? "—")}
                  </dd>
                </div>
              ))}
          </dl>
        </div>
      </section>

      {/* Parties */}
      <section>
        <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
          Parties ({allParties.length})
        </h3>
        <div className="space-y-3">
          {allParties.map((p) => (
            <div
              key={p.id}
              className="rounded-lg bg-gray-900 border border-gray-800 p-3"
            >
              <div className="flex items-center gap-2 mb-2">
                <span className="font-semibold text-sm text-gray-200">
                  {p.name}
                </span>
                <span className="text-xs text-gray-500 font-mono">{p.id}</span>
                <span className="ml-auto text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded">
                  {p.kind}
                </span>
              </div>
              {Object.keys(p.state).length > 0 ? (
                <div className="space-y-1">
                  {Object.entries(p.state).map(([k, v]) => (
                    <div key={k} className="flex gap-2 text-xs">
                      <span className="text-gray-500 font-mono w-36 shrink-0 truncate">
                        {k}
                      </span>
                      <span className="text-gray-300 font-mono break-all">
                        {String(v ?? "null")}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-gray-600">No state entries</p>
              )}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
