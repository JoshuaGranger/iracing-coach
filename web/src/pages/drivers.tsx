import { useEffect } from "preact/hooks";
import { api, paths } from "../core/api";
import { formatDate, plural } from "../core/format";
import { useResource } from "../core/resource";
import { href, navigate } from "../core/router";
import { Chip, Empty, ErrorLine, Loading, PageHead } from "../ui/kit";

/** D7: list of drivers that have data; one driver opens straight to that driver. */
export function DriversPage() {
  const res = useResource(paths.drivers, () => api.drivers());
  const drivers = res.data?.drivers;
  const only = drivers && drivers.length === 1 ? drivers[0]! : null;
  useEffect(() => {
    if (only) navigate({ page: "sessions", customerId: only.customer_id }, { replace: true });
  }, [only?.customer_id]);

  if (!drivers) return res.error ? <ErrorLine error={res.error} /> : <Loading what="drivers" />;
  if (only) return <Loading what="sessions" />;
  if (drivers.length === 0) {
    return (
      <Empty title="No drivers yet">
        Recordings appear here after the collector on the sim PC uploads its first session.
      </Empty>
    );
  }
  return (
    <>
      <PageHead title="Drivers" />
      <ul class="cards" aria-label="Drivers">
        {drivers.map((d) => (
          <li key={d.customer_id}>
            <a class="card card-link" href={href({ page: "sessions", customerId: d.customer_id })}>
              <span class="card-title">{d.name}</span>
              <span class="card-meta">
                {d.irating != null ? `${d.irating} iR · ` : ""}
                {plural(d.session_count, "session")} · last {formatDate(d.last_session_at)}
              </span>
              {d.live ? <Chip tone="good">Live</Chip> : null}
            </a>
          </li>
        ))}
      </ul>
    </>
  );
}
