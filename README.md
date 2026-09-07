# 🏈 NFL Fantasy Football Agent

AI-powered Fantasy Football analyst that manages two ESPN leagues, powered by Gemini 2.5 Flash.

## What It Does

- **Start/Sit Optimizer** — Game-theory based lineup recommendations considering matchup context, Vegas lines, injuries, and weather
- **Waiver Wire Engine** — Identifies breakout pickups and flags declining roster players
- **Trade Evaluator** — VORP-based trade analysis focused on your starting lineup impact
- **Weekly Matchup Previews** — Full scouting reports with opponent analysis
- **Injury Monitoring** — Practice report tracking with impact analysis
- **Scheduled Alerts** — Automated analysis delivered on the optimal day/time each week

## Leagues

| League | ID | Format | Passing TD | Key Difference |
| :--- | :--- | :--- | :--- | :--- |
| PNA 2026 | 991059191 | 12-team Full PPR | 4 pts | 2 FLEX, No Kicker |
| Chips Ahoy | 735288 | 10-team Full PPR | 6 pts | 1 FLEX, Has Kicker |

## Tech Stack

- **Python 3.14** — Core runtime
- **ESPN Fantasy API** (`espn-api`) — League data (read-only)
- **Gemini 2.5 Flash** — LLM reasoning (free tier via AI Studio)
- **Sleeper API** — Injury reports & trending adds/drops
- **nflverse** (`nflreadpy`) — Advanced stats (snap counts, target share, red zone)
- **Open-Meteo** — Game-day weather for outdoor stadiums
- **GCP Cloud Run** — Hosting (free tier)
- **GCP Cloud Scheduler** — Cron triggers (free tier)

## Setup

```bash
# Clone
git clone https://github.com/damiththa/nfl-fantasy-football-agent.git
cd nfl-fantasy-football-agent

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e ".[dev]"

# Configure credentials
cp .env.example .env
# Edit .env with your ESPN cookies and Gemini API key

# Run tests
pytest tests/ -v
```

## Cost

**\$0.00** — Every component runs within free tiers:
- ESPN API, Sleeper, nflverse, Open-Meteo: Free, no API keys
- Gemini 2.5 Flash: Free tier via AI Studio (500+ req/day, we use ~7)
- GCP Cloud Run: Free tier (2M invocations/month, we use <100)
- GCP Cloud Scheduler: 3 free jobs (we use exactly 3)

## Project Structure

```
src/
├── config.py              # League configs, constants, slot maps
├── main.py                # Cloud Run entry point (FastAPI)
├── espn/                  # ESPN Fantasy API integration
│   ├── client.py          # League connection & auth
│   ├── roster.py          # Roster parsing
│   ├── matchup.py         # Weekly matchups & box scores
│   ├── free_agents.py     # Free agent pool
│   └── transactions.py    # Trade history, waiver claims
├── data/                  # External NFL data sources
│   ├── injuries.py        # Sleeper API → injury status
│   ├── vegas.py           # ESPN Scoreboard → spreads, O/U
│   ├── stats.py           # nflverse → snap counts, targets
│   ├── trending.py        # Sleeper → waiver wire buzz
│   └── weather.py         # Open-Meteo → stadium weather
├── analysis/              # Core analytics engine
│   ├── lineup.py          # Start/Sit optimizer
│   ├── waivers.py         # Waiver target ranking
│   ├── trades.py          # Trade value calculator
│   ├── matchup_preview.py # Weekly matchup report
│   └── scoring.py         # Custom scoring engine
├── intelligence/          # Gemini LLM reasoning layer
│   ├── gemini_client.py   # Gemini API client
│   ├── prompts.py         # Prompt templates
│   └── schemas.py         # Pydantic response schemas
├── jobs/                  # Scheduled job definitions
│   ├── tuesday_waivers.py
│   ├── thursday_tnf.py
│   ├── friday_injuries.py
│   ├── saturday_matchup.py
│   ├── sunday_pregame.py
│   └── on_demand.py
└── notifications/         # Output delivery (Phase 5)
    └── base.py
```
