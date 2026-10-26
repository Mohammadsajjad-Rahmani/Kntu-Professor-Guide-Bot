# Deploy on PythonAnywhere

## 1) GitHub (on your own computer, in the project folder)
    git init
    git add .
    git commit -m "Initial commit"
    git branch -M main
    git remote add origin https://github.com/<you>/<repo>.git   # create an EMPTY PRIVATE repo first
    git push -u origin main
`.env` and `*.db` are in .gitignore: the token and the database never go to GitHub.

## 2) PythonAnywhere (Bash console)
    git clone https://github.com/<you>/<repo>.git vahedyar      # private repo: use a personal access token as password
    cd vahedyar
    python3.11 -m venv ~/venv-vahedyar      # needs Python >= 3.11 (pick 3.11+ in the account's system image)
    source ~/venv-vahedyar/bin/activate
    pip install -r requirements.txt
    cp .env.example .env && nano .env       # BOT_TOKEN, ADMIN_IDS, CHANNEL_ID, ... ; leave PROXY_URL EMPTY
    python -m bot.main                      # quick test, stop with Ctrl+C

## 3) Keep it running (Tasks tab -> Always-on tasks; needs a paid plan)
Command:
    /home/<user>/venv-vahedyar/bin/python -m bot.main
Working directory must be `/home/<user>/vahedyar` (so `.env` and the SQLite file are found).
If the task is set up as `cd /home/<user>/vahedyar && /home/<user>/venv-vahedyar/bin/python -m bot.main`, that works too.

## 4) Updating later
    cd ~/vahedyar && git pull
then restart the always-on task from the Tasks tab. New tables/columns are added automatically at startup.

## Backup
The database is the file `vahedyar.db` in the project folder. Copy it regularly (Files tab -> download).
