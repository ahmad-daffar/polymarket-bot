# Deploy to GitHub — 5 Minutes

This gets the bot running 24/7 on GitHub's free servers, with a live dashboard URL.

---

## Step 1 — Create a GitHub account (if you don't have one)
Go to https://github.com and sign up. It's free.

---

## Step 2 — Create a new repository
1. Go to https://github.com/new
2. Name it: `polymarket-bot`
3. Set it to **Private** (keeps your strategy private)
4. Click **Create repository** — do NOT add README or .gitignore

---

## Step 3 — Push your code (PowerShell)

```powershell
cd "C:\Users\Ahmad\Documents\Claude\Projects\polymarket ai\polymarket-bot"

git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/polymarket-bot.git
git push -u origin main
```

Replace `YOUR_USERNAME` with your GitHub username.

---

## Step 4 — Enable GitHub Pages
1. Go to your repo on GitHub
2. Click **Settings** → **Pages** (left sidebar)
3. Under "Source", select **Deploy from a branch**
4. Branch: `main`, Folder: `/docs`
5. Click **Save**

Your dashboard URL will be:
`https://YOUR_USERNAME.github.io/polymarket-bot`

---

## Step 5 — Run it manually the first time
1. Go to your repo → **Actions** tab
2. Click **Polymarket Bot** in the left sidebar
3. Click **Run workflow** → **Run workflow**
4. Watch it run (takes ~2 minutes)
5. After it finishes, open your GitHub Pages URL — your dashboard is live!

---

## That's it!

From now on the bot runs **automatically every 2 hours**, 24/7, for free.
Each run updates your dashboard URL with fresh data.

To check results anytime: `https://YOUR_USERNAME.github.io/polymarket-bot`

---

## Optional tweaks

**Change the run frequency** — edit `.github/workflows/bot.yml`:
```yaml
- cron: '0 */4 * * *'   # Every 4 hours
- cron: '0 */1 * * *'   # Every hour
```

**Run more wallets** — in `main.py`, change `--quick` to nothing in the workflow:
```yaml
run: python main.py --simulate --skip-dashboard
```
