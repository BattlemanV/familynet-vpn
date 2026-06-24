# FamilyNet VPN — краткая инструкция

Панель управления семейным VPN-сервером с 3 уровнями защиты: WireGuard, AmneziaWG и Xray (REALITY + XHTTP + WS).

## Установка

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/BattlemanV/familynet-vpn/main/install.sh)
```

При установке нужно выбрать один из вариантов:

| Уровень | Порт | Протокол | Для кого |
|---------|------|----------|----------|
| **1 — WireGuard** | 51820/udp | WG | Любое устройство, макс. скорость |
| **2 — AmneziaWG** | 31121/udp | AWG (обфускация) | Обход DPI, AmneziaVPN |
| **3 — Xray** | 443/tcp | REALITY | Windows/Android/macOS |
| | 8445/tcp | XHTTP | iOS (Hiddify) |
| | 8444/tcp | WS | iOS запасной |

## После установки

```
1. Отсканируй QR-код в приложении
2. Подключись к VPN
3. Открой http://10.8.0.1:8000
```

**Никакого пароля или токена не нужно.** Если ты admin VPN peer — панель открывается сразу.

Recovery token (для SSH/developer доступа) сохранён в `/root/familynet-vpn/api_token`.

## Скриншоты

| | |
|:-:|:-:|
| ![Главная](screenshots/photo_1_2026-06-19_15-05-44.jpg) | ![Устройства](screenshots/photo_2_2026-06-19_15-05-44.jpg) |
| ![Графики трафика](screenshots/photo_3_2026-06-19_15-05-44.jpg) | ![Настройки и бэкапы](screenshots/photo_4_2026-06-19_15-05-44.jpg) |
| ![Диагностика и логи](screenshots/photo_5_2026-06-19_15-05-44.jpg) | |

## Возможности

- управление клиентами (создать/удалить/переименовать)
- QR-коды и конфиги (WG, AWG, Xray VLESS-ссылки)
- роли (admin / user)
- статистика трафика
- журнал действий
- резервные копии / восстановление
- ограничение скорости
- родительский контроль (дневные лимиты, расписание)
- мониторинг CPU, RAM, disk, uptime
- перезагрузка VPS / рестарт VPN и панели
- аватары (эмодзи + фото)
- 7 языков интерфейса

## Документация

- [ARCHITECTURE.md](ARCHITECTURE.md) — архитектура
- [CONNECT.ru.md](CONNECT.ru.md) — подключение по шагам
- [INSTALLATION.md](INSTALLATION.md) — установка
- [API.md](API.md) — API endpoints
- [CHANGELOG.md](CHANGELOG.md) — история изменений
- [DATA-FORMATS.md](DATA-FORMATS.md) — форматы данных
