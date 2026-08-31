// Адресация разделов кабинета через `location.hash` (spec 2026-08-31 §раздел
// «Адресация»): сборка статическая (`output: "export"`), а `trailingSlash:
// false` менять нельзя — по ссылкам вида `/admin.html?token=...` уже ходят
// люди, отдельных страниц под разделы завести не получится.

export type Screen = "overview" | "clients" | "briefs" | "campaigns" | "channels";

export type Route =
  | { screen: "overview" }
  | { screen: "clients"; id: number | null }
  | { screen: "briefs"; id: number | null }
  | { screen: "campaigns" }
  | { screen: "channels" };

/** Строит hash для раздела/карточки — единственное место, где живёт формат. */
export const routeHash = {
  overview: () => "#/overview",
  clients: (id?: number) => (id != null ? `#/clients/${id}` : "#/clients"),
  briefs: (id?: number) => (id != null ? `#/briefs/${id}` : "#/briefs"),
  campaigns: () => "#/campaigns",
  channels: () => "#/channels",
};

function parseId(raw: string | undefined): number | null {
  if (!raw) return null;
  const id = Number(raw);
  return Number.isFinite(id) && id > 0 ? id : null;
}

export function parseRoute(hash: string): Route {
  const [section, sub] = hash.replace(/^#\/?/, "").split("/").filter(Boolean);

  switch (section) {
    case "clients":
      return { screen: "clients", id: parseId(sub) };
    case "briefs":
      return { screen: "briefs", id: parseId(sub) };
    case "campaigns":
      return { screen: "campaigns" };
    case "channels":
      return { screen: "channels" };
    default:
      return { screen: "overview" };
  }
}
