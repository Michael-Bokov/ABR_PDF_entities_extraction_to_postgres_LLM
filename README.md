# ABR Import — импорт перечней из сканов PDF в PostgreSQL

Автоматическое извлечение из PDF без текстового слоя (сканы):

- **Сокращения** (перечень сокращений)
- **Термины и определения**

Данные сохраняются в PostgreSQL со связью с документом; выборка по **названию документа** (`title` = имя файла без расширения).

## Архитектура

```
PDF → PaddleOCR (CPU) → текст по страницам → Ollama (GPU) → JSON → PostgreSQL
```

| Сервис | Роль |
|--------|------|
| **worker** | OCR на CPU, чанки по 4 стр., запросы в Ollama |
| **ollama** | Квантованная LLM на GPU (llama3.1:8b и др.) |
| **postgres** | Хранение |


## Быстрый старт

1. Положите PDF или ZIP в `data/input/`.

2. Запуск ([NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) для Ollama):

```bash
docker compose up -d --build
docker compose exec ollama ollama pull llama3.1:8b
docker compose exec worker python -m abr_import check
```

3. Логи воркера:

```bash
docker compose logs -f worker
```

4. Запрос перечней по названию документа:

```bash
docker compose exec worker python -m abr_import query "Имя_документа"
```
## Схема БД

- `documents` — метаданные, `file_hash` (идемпотентность), `status`, `title`
- `abbreviations` — `short_form`, `full_form`
- `terms` — `term`, `definition`
- `processing_log` — журнал обработки

## Отказоустойчивость

- Уже **completed** документ с тем же хешем не обрабатывается повторно.
- При падении во время `processing` запись сбрасывается в `pending` после `STALE_PROCESSING_MINUTES` (по умолчанию 120 мин).
- Успешно обработанные PDF перемещаются в `data/archive/`.
- Повторный запуск контейнера: воркер подхватывает только `pending` и `failed`.

## Переменные окружения

См. `.env.example`.

| Переменная | Описание |
|------------|----------|
| `DATABASE_URL` | PostgreSQL |
| `INPUT_DIR` | Каталог входных PDF/ZIP |
| `USE_GPU` | PaddleOCR на GPU |
| `PDF_DPI` | Качество рендера (200 по умолчанию) |
| `STALE_PROCESSING_MINUTES` | Таймаут зависшей обработки |


### Повторный импорт тех же документов

1. Остановите воркер: `docker compose stop worker`
2. Удалите **папку распаковки** (имя = имя архива без `.zip`):

   ```
   data/input/ИмяАрхива/
   ```

3. Удалите маркер «уже распаковано»:

   ```
   data/input/ИмяАрхива.zip.done
   ```

4. Верните PDF из архива обработки, если воркер уже перенёс их:

   ```
   data/archive/*.pdf  →  data/input/
   ```

   (или снова положите исходный `.zip` в `data/input/`)

5. Очистите записи в БД (иначе останутся старые `title`/`filename`):
   
6. Запустите снова: `docker compose start worker`

## Модели на 2080 Ti 11 GB

| Модель | VRAM | Команда |
|--------|------|---------|
| **llama3.1:8b** (по умолчанию) | ~5–6 GB | `ollama pull llama3.1:8b` |
| **qwen2.5:14b-instruct-q4_K_M** | ~9 GB | `ollama pull qwen2.5:14b-instruct-q4_K_M` + `OLLAMA_MODEL=...` |


## Переменные

| Переменная | Описание |
|------------|----------|
| `USE_GPU` | `false` — OCR на CPU |
| `EXTRACTOR` | `llm` или `rules` |
| `OLLAMA_URL` | `http://ollama:11434` |
| `OLLAMA_MODEL` | имя модели в Ollama |
| `LLM_CHUNK_PAGES` | страниц на один запрос (4) |

## Ограничения

- Качество = OCR × LLM; на плохих сканах возможны пропуски и галлюцинации.
- Пустой результат → warning в `processing_log`, статус `completed` с 0 строк.

## Структура проекта

```
src/abr_import/
  ocr/          # PDF → изображения, PaddleOCR
  llm/          # Ollama, чанки, промпт
  extract/      # rules-режим (regex)
  pipeline/     # ZIP, обработка
  db/           # SQLAlchemy
sql/init.sql
```

Подпакеты без `__init__.py` — импорты явные (`abr_import.ocr.engine`).
