"use client";

// «Каналы доставки»: просмотр состояния юзербота и kotbot — веб-зеркало команд
// `/userbot_status` и `/kotbot` бота (`GET /admin/channels`,
// `services/channels_status.py`). Только просмотр: подключение аккаунта (код из
// SMS, пароль 2FA) сознательно остаётся в боте (spec 2026-08-31, задача 2).
//
// Три состояния честно, не два: «не настроен» (пустой *_BASE_URL на сервере —
// это НЕ ошибка, канал просто не подключён администратором), «настроен, но
// недоступен» (сервис не отвечает — это уже плохо) и «работает». Текст и деление
// на три состояния — те же, что в `bot/handlers/userbot_status.py` /
// `services/userbot_report.py::render_status` и `bot/handlers/link_kotbot.py`.

import type { ChannelsOut, UserbotChannel, UserbotSession, KotbotChannel } from "@/lib/adminApi";
import { useAdminResource } from "@/lib/useAdminResource";

import { Badge } from "../ui/Badge";
import { ErrorState } from "../ui/ErrorState";
import { Row } from "../ui/Row";
import { SkeletonRows } from "../ui/Skeleton";

function UserbotSessionRow({ session }: { session: UserbotSession }) {
  const who = session.phone_masked
    ? `Юзербот — оператор ${session.sender_id} (${session.phone_masked})`
    : `Юзербот — оператор ${session.sender_id}`;

  if (session.authorized) {
    return <Row title={who} subtitle="Работает." badge={<Badge tone="accent">работает</Badge>} />;
  }
  if (session.unreachable) {
    return (
      <Row
        title={who}
        subtitle="Telegram недоступен с сервера — переподключение не поможет, бот пробует сам."
        badge={<Badge tone="warn">недоступен</Badge>}
      />
    );
  }
  return (
    <Row
      title={who}
      subtitle="Не подключена. Подключить можно в боте: команда /link_userbot."
      badge={<Badge>не подключена</Badge>}
    />
  );
}

function UserbotBlock({ channel }: { channel: UserbotChannel }) {
  if (!channel.configured) {
    return (
      <Row
        title="Юзербот"
        subtitle="Не настроен на этом сервере — это не ошибка, канал просто не подключён."
        badge={<Badge>не настроен</Badge>}
      />
    );
  }
  if (!channel.available) {
    return (
      <Row
        title="Юзербот"
        subtitle="Сервис не отвечает — состояние сессий сейчас неизвестно. Брифы можно отправлять на email."
        badge={<Badge tone="danger">недоступен</Badge>}
      />
    );
  }
  if (!channel.sessions.length) {
    return (
      <Row
        title="Юзербот"
        subtitle="Ни одной сессии не подключено. Подключить можно в боте: команда /link_userbot."
        badge={<Badge>не подключён</Badge>}
      />
    );
  }
  return (
    <>
      {channel.sessions.map((session) => (
        <UserbotSessionRow session={session} key={session.sender_id} />
      ))}
    </>
  );
}

function KotbotBlock({ channel }: { channel: KotbotChannel }) {
  if (!channel.configured) {
    return (
      <Row
        title="kotbot"
        subtitle="Не настроен на этом сервере — это не ошибка, канал просто не подключён."
        badge={<Badge>не настроен</Badge>}
      />
    );
  }
  if (!channel.healthy) {
    return (
      <Row
        title="kotbot"
        subtitle="Настроен, но сейчас недоступен."
        badge={<Badge tone="danger">недоступен</Badge>}
      />
    );
  }
  return <Row title="kotbot" subtitle="Работает." badge={<Badge tone="accent">работает</Badge>} />;
}

export function DeliveryChannels() {
  const [state, retry] = useAdminResource<ChannelsOut>("/channels");

  if (state.status === "loading") return <SkeletonRows count={2} />;
  if (state.status === "error") {
    return (
      <ErrorState message="Не удалось загрузить состояние каналов доставки." onRetry={retry} />
    );
  }

  return (
    <div className="adm-list">
      <UserbotBlock channel={state.data.userbot} />
      <KotbotBlock channel={state.data.kotbot} />
    </div>
  );
}
