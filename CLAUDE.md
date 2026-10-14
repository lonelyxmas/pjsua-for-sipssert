# pjsua-test

Docker-образ на базе PJSIP для тестирования SIP over TLS с валидацией RTP/SRTP.
Используется с [SIPssert](https://github.com/OpenSIPS/SIPssert) как замена SIPp.

## Контекст проекта

Проект создан для решения архитектурных ограничений SIPp v3.7:
- TLS-роль жёстко привязана к SIP-роли (UAC=TLS client, UAS=TLS server)
- Низкая производительность SRTP-валидации (кастомная JLSRTP вместо libsrtp)
- Глобальные SRTP-контексты (не per-call)

Подробности: [README.md](README.md)

## Структура

```
Dockerfile              — Alpine 3.24 + пакеты pjproject 2.17 + py3-yaml
entrypoint.sh           — CLI-обёртка (режимы uac, uas, uas-tls-client, uac-tls-server)
scripts/
  common.py             — общий модуль: EchoValidatorPort, OptionsPingManager, HeaderManager,
                          ConfigLoader, add_common_args, BYE/re-INVITE/OPTIONS helpers
  uac.py                — PJSUA2: SIP UAC + TLS client + echo-валидация + заголовки
  uas.py                — PJSUA2: SIP UAS + TLS server + echo-валидация + заголовки
  uas_tls_client.py     — PJSUA2: SIP UAS + TLS client + echo-валидация + заголовки
  uac_tls_server.py     — PJSUA2: SIP UAC + TLS server + echo-валидация + заголовки
tests/                  — sipssert интеграционные тесты (14 сценариев)
  pjsua-standard-roles/ — стандартные роли uac + uas
  pjsua-tls-roles/      — развязка TLS-ролей: uac-tls-server + uas-tls-client
  pjsua-config-file/    — YAML-конфиг (--config)
  pjsua-headers-set-check/    — set-header + expect-header-value
  pjsua-headers-expect-not/   — expect-no-header
  pjsua-headers-regex/        — expect-header-regex + expect-header-count
  pjsua-headers-tls-roles/    — заголовки с развязкой TLS-ролей
  pjsua-udp-transport/  — UDP транспорт (--transport=udp)
  pjsua-tcp-transport/  — TCP транспорт (--transport=tcp)
  pjsua-bye-from-uas/   — BYE от UAS стороны
  pjsua-bye-none/       — без BYE
  pjsua-reinvite-uac/   — re-INVITE от UAC
  pjsua-reinvite-uas/   — re-INVITE от UAS
  pjsua-options-ping/   — in-dialog OPTIONS ping (⚠ всегда FAIL — ограничение pjsua2, см. Known Issues README)
```

Юнит-тесты (80 тестов, pure-python — запускаются ВНУТРИ контейнера, вне его
нужен мок pjsua2):

```bash
docker run --rm --entrypoint python3 -v "$PWD/tests:/tests:ro" pjsua-test:new \
    -m unittest discover -s /tests -p "test_common.py"
```

## Ключевые решения

- **Все 4 режима на PJSUA2** — uac, uas, uas-tls-client, uac-tls-server реализованы Python-скриптами через PJSUA2 API (не pjsua CLI)
- **scripts/common.py** — единый shared-модуль для всех скриптов; содержит EchoValidatorPort, HeaderManager, ConfigLoader, add_common_args и вспомогательные функции
- **ConfigLoader** — загрузка YAML-конфига (`--config=FILE`); CLI-аргументы имеют приоритет; списки заголовков (`headers:`) объединяются (merge)
- **HeaderManager** — управляет 8 типами проверок SIP-заголовков (set, expect, expect_not, expect_name_regex, expect_not_regex, expect_value, expect_value_regex, expect_count); поддерживает индексирование (Name[0], Name[-1]) и диапазоны count (N, N+, N-M)
- **EchoValidatorPort** — кастомный `AudioMediaPort` для проверки RTP/SRTP:
  детерминированные μ-law-stable паттерны, разделённые по направлениям (UAC —
  низкая полоса амплитуд, UAS — высокая; окна матчинга не пересекаются →
  детект перепутывания/подмены потоков), матчинг по медиане кадра с окном
  аттенюации /12…+2%; PASS = match ≥ tolerance И received ≥ min_received_pct
  (флор от «аудио умерло в середине вызова»); log_srtp_status предупреждает
  о SDES-даунгрейде
- **--bye=uac|uas|none** — контроль стороны отправки BYE; `--wait-bye` задаёт таймаут ожидания BYE от удалённой стороны
- **--reinvite-by / --reinvite-delay** — отправка re-INVITE через заданные задержки; onCallMediaState переподключает EchoValidatorPort
- **--options-ping / --options-auto-reply / --options-tolerance** — in-dialog OPTIONS keepalive с валидацией ответов; OptionsPingManager в common.py.
  ⚠️ Ответная сторона (options-auto-reply) НЕ реализуема на pjsua2: входящий
  in-dialog OPTIONS отклоняется библиотекой (500 «Unhandled by dialog usages»,
  sip_dialog.c) до app-кода — колбэка/API для ответа в pjsua2 нет (проверено
  на 2.14.1/2.17/master). Сценарий pjsua-options-ping всегда FAIL.
  Keepalive из реальных АТС — out-of-dialog, pjsua отвечает штатно.
- **--transport** — параметр для выбора транспорта (tls, tcp, udp, default: tls); uac/uas поддерживают все 3; режимы с TLS-ролями всегда используют TLS. В TRANSPORT_MAP (entrypoint.sh) отображаются значения на порты и флаги.
- **Probe INVITE** — uas-tls-client отправляет probe для сигнализации о TLS-готовности uac-tls-server
- **transportId** — аккаунты привязаны к конкретным TLS-транспортам для корректной маршрутизации
- **os._exit()** — обход segfault в PJSUA2 Python bindings при libDestroy()
- **Быстрый shutdown + SIGTERM-handler** (совместимость с pjproject 2.17 / alpine 3.24):
  на 2.17 libDestroy занимает ~1.5s и onCallState(DISCONNECTED) у uas приходит
  с задержкой ~2s после BYE (на 2.14 — мгновенно); sipssert же делает
  daemon-контейнерам docker stop(timeout=0) сразу после выхода клиента →
  uas получал SIGKILL (137) и сценарии падали на teardown. Контрмеры:
  wait_call_end() опрашивает состояние вызова в дополнение к событию,
  safe_shutdown() пропускает hangupAll/destroy при уже завершённом вызове,
  install_signal_exit() даёт чистый exit 0 по SIGTERM (важно для PID 1:
  без обработчика ядро не доставляет SIGTERM вообще),
  tests/config.yml задаёт stop_timeout: 5
- **Фазово-независимый эхо-паттерн + ре-анкор re-INVITE** (валидация RTP/SRTP):
  паттерн `EchoValidatorPort` — короткий цикл (по 6 значений на сторону:
  UAC — низкая полоса, UAS — высокая; оба знака) полностью
  покрытый ring buffer 64; иначе одностороннее окно фазы выпадало при
  пересоздании медиа-сессии на re-INVITE и все кадры после него считались
  mismatch. Входящий re-INVITE без `a=crypto` (кейс rtpengine-passthrough)
  отклоняется с 488 ДО media re-init (pjproject 2.17 при неудавшейся
  ренеготиации подменяет активный SRTP RX-ключ → входящий аудио умирает в
  тишину), затем через 0.2с отправляется свой re-INVITE для ре-анкора медиа-ноги
  (RFC 3261 §14.2); окно матча имеет запас +2% сверху под requantизацию G.711
- Exit code `0`/`1` для совместимости с sipssert

## Интеграция с sipssert

- Образ: `pjsua-test`
- sipssert монтирует директорию сценария в `/home` (read-only)
- Аргументы: `--key=value` формат (YAML list в scenario.yml)
- YAML-конфиг: `--config=/home/config.yml`; конфиг-файл удобно класть рядом со scenario.yml
- Аргументы заголовков: `--set-header`, `--expect-header`, `--expect-header-value` и др. (8 типов); дополняют списки из `--config`
- При host-сети: использовать `--rtp-port` для разных RTP-портов на каждом контейнере
- Результат теста определяется exit code контейнера
- `daemon: true` для серверной стороны, `require: [{started: ...}]` для клиента
