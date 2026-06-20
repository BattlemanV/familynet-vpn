# FamilyNet VPN — краткая инструкция

Панель управления семейным VPN-сервером на WireGuard.

## Установка

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/BattlemanV/familynet-vpn/main/install.sh)
```

Скрипт установит Docker, клонирует репозиторий, запустит контейнер, создаст первого админа и покажет QR-код.

## После установки

```
1. Отсканируй QR-код в WireGuard приложении
2. Подключись к VPN
3. Открой http://10.8.0.1:8000
```

**Никакого пароля или токена не нужно.** Если ты admin VPN peer — панель открывается сразу.

Recovery token (для SSH/developer доступа) сохранён в `/root/wg-admin-api/api_token`. Не показывается на экране.

## Скриншоты

| | |
|:-:|:-:|
| ![Главная](screenshots/photo_1_2026-06-19_15-05-44.jpg) | ![Устройства](screenshots/photo_2_2026-06-19_15-05-44.jpg) |
| ![Графики трафика](screenshots/photo_3_2026-06-19_15-05-44.jpg) | ![Настройки и бэкапы](screenshots/photo_4_2026-06-19_15-05-44.jpg) |
| ![Диагностика и логи](screenshots/photo_5_2026-06-19_15-05-44.jpg) | |

## Возможности

- управление клиентами (создать/удалить/переименовать)
- QR-коды и конфиги
- роли (admin / user)
- статистика трафика
- журнал действий
- резервные копии / восстановление
- ограничение скорости
- родительский контроль (дневные лимиты, расписание)
- мониторинг CPU, RAM, disk, uptime
- перезагрузка VPS / рестарт WireGuard и панели
- аватары (эмодзи + фото)
- 7 языков интерфейса

## Документация (на английском)

- [ARCHITECTURE.md](ARCHITECTURE.md) — архитектура
- [INSTALLATION.md](INSTALLATION.md) — установка
- [API.md](API.md) — API endpoints
- [CHANGELOG.md](CHANGELOG.md) — история изменений
- [DATA-FORMATS.md](DATA-FORMATS.md) — форматы данных
