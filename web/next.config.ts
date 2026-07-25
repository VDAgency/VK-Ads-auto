import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Статический экспорт: в проде нет Node-процесса. Ядро (FastAPI) отдаёт готовые
  // файлы из web/out ровно так же, как раньше отдавало web/static.
  output: "export",

  // НЕ включать trailingSlash. При false роут /cabinet экспортируется в файл
  // out/cabinet.html — это сохраняет уже разосланные клиентам ссылки вида
  // /cabinet.html?token=... (magic-link из писем и Telegram) и /brief-*.html?t=...
  // При true получилось бы out/cabinet/index.html, и все живые ссылки перестали
  // бы открываться.
  trailingSlash: false,

  // next/image требует рантайм-оптимизатор, которого при статическом экспорте нет.
  images: { unoptimized: true },
};

export default nextConfig;
