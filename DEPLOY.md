# Deploy XAUUSD Bot to Fly.io (Free, No Credit Card)

## What you need
- A GitHub account (free)
- A Fly.io account (free, no credit card)
- 10 minutes

## Step 1: Create GitHub repo

1. Go to https://github.com/new
2. Name it: `xau-bot`
3. Set to **Public**
4. Click **Create repository**

## Step 2: Upload bot code to GitHub

On your local machine, open terminal:

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/xau-bot.git
cd xau-bot

# Copy bot files (from your Downloads or wherever you saved them)
cp -r /home/sanjay/xau_bot/* .

# Push to GitHub
git add .
git commit -m "Initial deploy"
git push origin main
```

## Step 3: Install Fly.io CLI

```bash
curl -L https://fly.io/install.sh | sh
export PATH="$HOME/.fly/bin:$PATH"
```

## Step 4: Deploy

```bash
# Login (opens browser, create free account)
flyctl auth signup

# Launch the app
flyctl launch

# Deploy
flyctl deploy
```

That's it! Your bot is live at `https://xau-bot.fly.dev`

## Step 5: Verify

```bash
# Check status
flyctl status

# View logs
flyctl logs

# Open dashboard
open https://xau-bot.fly.dev
```

## Common Commands

| Command | What it does |
|---|---|
| `flyctl logs` | View live logs |
| `flyctl status` | Check bot status |
| `flyctl restart` | Restart the bot |
| `flyctl deploy` | Redeploy after config changes |
| `flyctl scale count 1` | Ensure 1 instance always running |

## Troubleshooting

**Bot sleeping?**
```bash
flyctl scale count 1
flyctl autoscale set min=1
```

**Config changes?**
Edit `config.json` in the repo, then:
```bash
git add config.json
git commit -m "Update config"
git push
flyctl deploy
```
