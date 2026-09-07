# 🏈 Mad Dawg's NFL Fantasy Football Agent

AI-powered senior Fantasy Football analyst managing two ESPN leagues on **Google Cloud Run** with **\$0.00 idle cost**, powered by **Gemini 2.5 Pro** and hosted on **~90% clean renewable wind energy** in `us-central1`.

---

## 🌐 Live Web Command Center (Primary On-Demand Access)

You can run your agent anytime directly from your phone or browser without touching a terminal:

### 👉 **[Open Web Portal](https://fantasy-agent-3pky7gmu6q-uc.a.run.app)** (`/`)
* **🎯 Optimize PNA 2026 Lineup**: One-click start/sit recommendations tailored to 2-FLEX, 4pt pass TD scoring.
* **🎯 Optimize Chips Ahoy Lineup**: One-click lineup analysis tailored to 6pt pass TD scoring and kicker strategy.
* **📋 Run Weekly Routine**: Executes waivers, Thursday Night Football lock checks, or Friday injury roundups.
* **🚨 Run Sunday Pregame Check**: 90-minute kickoff inactives, weather checks, and final lineup locks.
* **🤝 Instant Trade Evaluator**: Enter players you give and receive for an immediate VORP starting-lineup verdict (ACCEPT / REJECT / COUNTER).

### 🛠️ Interactive API Documentation (`/docs`)
* Explore or test individual endpoints directly via Swagger UI: **[Open Swagger Portal](https://fantasy-agent-3pky7gmu6q-uc.a.run.app/docs)**.

---

## 📅 Weekly Order of Operations (Manager Playbook)

Follow this schedule every week to maximize your championship edge:

```
TUESDAY (Morning)
  ├── 1. Review Waiver Report (automated trigger at 7:00 AM ET)
  └── 2. Submit Waiver Claims on ESPN before Tuesday night lock

WEDNESDAY (Morning)
  ├── 3. Confirm processed waiver pickups
  └── 4. Scout un-claimed Free Agents (free additions without using waiver priority)

TUESDAY – THURSDAY (Afternoon)
  └── 5. Propose & Negotiate Trades before Thursday kickoff

THURSDAY (6:30 PM ET - 90 mins before TNF)
  ├── 6. Check Thursday Night Football inactives
  └── 7. GOLDEN RULE: Never put a Thursday player in a FLEX slot!
         (Always put them in RB or WR to keep your FLEX open for Sunday)

FRIDAY (4:00 PM ET)
  └── 8. Review official Friday Practice Participation (DNP / LP / FP)
         (Flags which Questionable players are actually trending toward playing)

SATURDAY (Morning)
  └── 9. Review Weekly Matchup Scouting Report & Vegas game totals

SUNDAY (11:30 AM ET - 90 mins before 1:00 PM kickoff)
  ├── 10. Review Sunday Pregame Inactive Report
  └── 11. Finalize early starters (swap out surprise inactives or bad weather games)

SUNDAY (2:30 PM ET)
  └── 12. Quick check on late-afternoon (4:05 / 4:25 PM) inactives
```

---

## 🏆 Managed Leagues

| League | ID | Format | Passing TD | Roster Construction |
| :--- | :--- | :--- | :--- | :--- |
| **PNA 2026** | `991059191` | 12-team Full PPR | 4 pts | 1 QB, 2 RB, 2 WR, 1 TE, **2 FLEX**, 1 DST, **No Kicker** |
| **Chips Ahoy** | `735288` | 10-team Full PPR | **6 pts** | 1 QB, 2 RB, 2 WR, 1 TE, **1 FLEX**, 1 DST, **1 Kicker** |

---

## 💻 Terminal Commands (Secondary Access)

If you prefer using the command line:

```bash
# Health & Status
curl https://fantasy-agent-3pky7gmu6q-uc.a.run.app/health

# On-Demand Lineup Optimization (PNA 2026)
curl -X POST "https://fantasy-agent-3pky7gmu6q-uc.a.run.app/query/lineup?league_id=991059191"

# On-Demand Lineup Optimization (Chips Ahoy)
curl -X POST "https://fantasy-agent-3pky7gmu6q-uc.a.run.app/query/lineup?league_id=735288"

# On-Demand Trade Evaluation
curl -X POST "https://fantasy-agent-3pky7gmu6q-uc.a.run.app/query/trade" \
     -H "Content-Type: application/json" \
     -d '{"league_id": 991059191, "giving_players": ["Player A"], "receiving_players": ["Player B"]}'
```

---

## 🔒 Security & Secrets

* **Zero Hardcoded Secrets**: ESPN session cookies (`ESPN_S2`, `ESPN_SWID`) and API keys are stored in **Google Cloud Secret Manager**.
* **Zero Cost**: Scales to absolute zero instances when not executing requests. Monthly cost: **\$0.00**.
