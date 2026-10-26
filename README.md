# vahedyar

Telegram bot for student reviews of professors (and, later, course materials).

## Setup

```bash
uv sync                          # creates .venv and installs dependencies
cp .env.example .env             # then put your BOT_TOKEN in .env
```

## Load the professors list

Edit `data/sample_professors.csv` (or make your own with the same columns), then:

```bash
uv run python -m scripts.import_csv data/sample_professors.csv --university "دانشگاه خواجه نصیر"
```

Replace the university name with yours. Running it again is safe: nothing gets duplicated.

CSV columns: `name`, `track`, `courses` (courses separated by `;`).

## Run the bot

```bash
uv run python -m bot.main
```

Telegram is filtered in Iran: you need a working connection (or a VPS outside Iran) for the bot to reach the Telegram API.

## Structure

```
bot/
  config.py        settings from .env
  main.py          bot entry point (currently: browse track -> professor -> course)
  db/models.py     all tables
  db/session.py    async engine and session
  utils/text.py    Persian text normalization
scripts/
  import_csv.py    import professors and courses
data/
  sample_professors.csv
```

## Next steps

1. Review form with buttons (aiogram FSM)
2. Automatic checks for free text, publishing to the channel
3. Search, reports, materials upload

## Collaborators (phase B)

Owners are the IDs in `ADMIN_IDS`. A student can tap «🤝 همکاری با ما» in the main menu, pick a faculty
and send a short note. The owner gets the request and chooses: only review moderation, only
professor/course management, or both. A collaborator sees and notifies only for their own faculty.
Banning, statistics, publishing, collaborator management and creating/renaming faculties stay owner-only.
