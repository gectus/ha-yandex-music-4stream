# Мульти-аккаунт: привязка Яндекс.Музыки к пользователям HA

## Проблема

В Home Assistant может быть несколько пользователей (например, муж и жена), у каждого свой аккаунт Яндекс.Музыки с персональными плейлистами и рекомендациями. При этом физические устройства 4STREAM общие — оба пользователя хотят воспроизводить музыку на одних и тех же колонках.

Текущая реализация использует один токен Яндекс.Музыки на всю интеграцию. Нужно: кто нажал play — тот аккаунт и используется.

## Как HA идентифицирует пользователя

Когда пользователь взаимодействует с UI (нажимает play, выбирает трек в Media Browser), HA создаёт service call с объектом `context`, содержащим `user_id` — уникальный идентификатор пользователя HA. Этот контекст доступен в entity через `self._context.user_id` в момент вызова метода (например, `async_play_media()`).

---

## Вариант A: Автоматический выбор по контексту (рекомендуемый)

### Идея

Хранить маппинг `HA user_id → Yandex Music токен`. При воспроизведении определять пользователя из контекста вызова и использовать соответствующий клиент Яндекс.Музыки.

### Структура данных config entry

```python
{
    "devices": [
        {"host": "192.168.1.27", "name": "Гостиная"},
        {"host": "192.168.1.7", "name": "Душевая"},
    ],
    "default_token": "y0_xxx...",       # токен по умолчанию (первый настроенный)
    "user_tokens": {                     # маппинг HA user → Yandex токен
        "ha_user_id_1": "y0_aaa...",
        "ha_user_id_2": "y0_bbb...",
    },
}
```

### Логика выбора клиента

```python
class YandexMusic4StreamPlayer(MediaPlayerEntity):

    def __init__(self, ...):
        self._ym_clients: dict[str, YandexMusicClient] = {}  # user_id → client
        self._default_ym: YandexMusicClient = ...             # клиент по умолчанию

    def _get_ym_for_context(self) -> YandexMusicClient:
        """Получить клиент Яндекс.Музыки для текущего пользователя."""
        user_id = self._context.user_id if self._context else None
        if user_id and user_id in self._ym_clients:
            return self._ym_clients[user_id]
        return self._default_ym

    async def async_play_media(self, media_type, media_id, **kwargs):
        ym = self._get_ym_for_context()
        # далее используем ym для поиска, получения URL и т.д.
        ...

    async def async_browse_media(self, ...):
        ym = self._get_ym_for_context()
        # browse показывает плейлисты текущего пользователя
        ...
```

### Настройка: Config Flow + Options Flow

**Первоначальная настройка (Config Flow):**
1. Ввод первого токена Яндекс.Музыки → становится `default_token`
2. Добавление устройств 4STREAM по IP
3. Подтверждение

**Привязка аккаунтов к пользователям (Options Flow, после установки):**
1. Пользователь открывает настройки интеграции
2. Выбирает пользователя HA из выпадающего списка
3. Вводит токен Яндекс.Музыки для этого пользователя
4. Интеграция проверяет токен и сохраняет маппинг

Список пользователей HA можно получить через:
```python
users = await hass.auth.async_get_users()
user_list = {user.id: user.name for user in users if not user.system_generated}
```

### Хранение клиентов

```python
# В __init__.py при загрузке entry
ym_clients = {}

# Аутентификация default клиента
default_client = YandexMusicClient(entry.data["default_token"])
await default_client.authenticate()

# Аутентификация клиентов для каждого пользователя
for ha_user_id, token in entry.data.get("user_tokens", {}).items():
    client = YandexMusicClient(token)
    await client.authenticate()
    ym_clients[ha_user_id] = client

hass.data[DOMAIN][entry.entry_id] = {
    "default_ym": default_client,
    "ym_clients": ym_clients,
    "proxy": proxy,
}
```

### Плюсы

- Полностью прозрачно для пользователя — не нужно ничего переключать
- Каждый видит свои плейлисты и рекомендации
- Работает автоматически из UI

### Минусы

- `context.user_id` может быть `None` в автоматизациях и скриптах — нужен fallback на default
- MediaSource (`media_source.py`) не получает контекст пользователя — в панели «Мультимедиа» всегда отображается default аккаунт
- Требует Options Flow для управления маппингом

### Ограничение MediaSource

`MediaSource.async_browse_media()` получает `MediaSourceItem`, который не содержит информации о пользователе. Это значит, что в глобальной панели «Мультимедиа» (боковое меню) всегда будет показан контент default аккаунта.

Однако entity-level `async_browse_media()` (через more-info dialog конкретного устройства) вызывается через service call с контекстом — там можно показать правильный аккаунт.

---

## Вариант B: Ручной выбор через Source

### Идея

Использовать механизм `source` / `source_list` из `MediaPlayerEntity`. Каждый аккаунт Яндекс.Музыки — это «источник». Пользователь вручную выбирает свой аккаунт перед воспроизведением.

### Реализация

```python
class YandexMusic4StreamPlayer(MediaPlayerEntity):

    _attr_source_list: list[str] = []  # ["Денис", "Жена"]

    def __init__(self, ...):
        self._ym_clients: dict[str, YandexMusicClient] = {}  # source_name → client
        self._current_source: str = ...                        # текущий выбранный

    @property
    def source(self) -> str:
        return self._current_source

    async def async_select_source(self, source: str) -> None:
        if source in self._ym_clients:
            self._current_source = source
            self._ym = self._ym_clients[source]
```

В UI на карточке media_player появится выпадающий список с именами аккаунтов.

### Плюсы

- Просто в реализации
- Работает везде — UI, автоматизации, скрипты
- Пользователь явно видит, чей аккаунт активен
- Не зависит от контекста

### Минусы

- Нужно вручную переключать аккаунт перед воспроизведением
- Легко забыть переключить — будет играть чужие рекомендации
- Если второй пользователь не переключил — слушает чужой аккаунт

---

## Вариант C: Гибридный (A + B)

### Идея

Комбинация обоих подходов:
1. По умолчанию аккаунт выбирается автоматически по `context.user_id` (вариант A)
2. Source-selector позволяет переключить вручную, если нужно (вариант B)
3. Ручной выбор через source перезаписывает автоматический выбор до следующего переключения

### Логика

```python
def _get_ym_for_context(self) -> YandexMusicClient:
    # 1. Если source выбран вручную — использовать его
    if self._manual_source:
        return self._ym_clients[self._manual_source]

    # 2. Попробовать определить по контексту
    user_id = self._context.user_id if self._context else None
    if user_id and user_id in self._ym_clients:
        return self._ym_clients[user_id]

    # 3. Fallback на default
    return self._default_ym
```

### Плюсы

- Лучшее из обоих вариантов
- Автоматика для UI, ручной контроль для автоматизаций

### Минусы

- Сложнее в реализации
- Поведение может быть неочевидным (когда автомат, когда ручной?)

---

## Рекомендация

**Начать с варианта A** (автоматический по контексту) — он покрывает основной use case (два человека пользуются через UI). Добавить source-selector (вариант C) позже, если понадобится ручное переключение для автоматизаций.

### План реализации

1. Добавить `user_tokens` в структуру config entry
2. Создать Options Flow для привязки токенов к пользователям HA
3. Аутентифицировать несколько `YandexMusicClient` при старте
4. В `async_play_media()` и `async_browse_media()` выбирать клиент по `context.user_id`
5. Fallback на default токен когда контекст недоступен
