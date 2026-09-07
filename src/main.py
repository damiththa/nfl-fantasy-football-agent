"""
FastAPI application serving the NFL Fantasy Football Agent on Google Cloud Run.
Exposes endpoints for Cloud Scheduler cron triggers and on-demand analysis queries.
"""

import logging
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from src.analysis.lineup import optimize_lineup
from src.analysis.matchup_preview import generate_matchup_preview
from src.analysis.trade_finder import propose_league_trades
from src.analysis.trades import evaluate_trade
from src.analysis.waivers import evaluate_waivers
from src.config import ALL_LEAGUES, get_current_season, get_gemini_model
from src.data.injuries import get_injury_report
from src.data.trending import fetch_trending_adds
from src.data.vegas import fetch_week_odds
from src.data.weather import fetch_game_weather
from src.espn.client import LeagueClient
from src.espn.matchup import get_current_week, get_weekly_matchup
from src.espn.roster import parse_roster
from src.intelligence.gemini_client import GeminiIntelligenceClient
from src.notifications.email import send_digest_email

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fantasy_agent")

app = FastAPI(
    title="NFL Fantasy Football Agent",
    description="AI-powered Fantasy Football veteran analyst for ESPN leagues, powered by Gemini Pro.",
    version="1.0.0",
)


class TradeRequest(BaseModel):
    league_id: int
    giving_players: list[str]
    receiving_players: list[str]


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Mad Dawg's Fantasy Football Command Center</title>
  <style>
    :root {
      --primary: #d9381e;
      --primary-dark: #b72b15;
      --bg: #0f172a;
      --card-bg: #1e293b;
      --border: #334155;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --success: #22c55e;
      --accent: #38bdf8;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      background-color: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding: 24px 16px;
    }
    .container { max-width: 960px; margin: 0 auto; }
    header {
      text-align: center;
      margin-bottom: 28px;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--border);
    }
    header h1 { font-size: 28px; color: var(--primary); margin-bottom: 6px; }
    header p { color: var(--text-muted); font-size: 14px; }
    .status-bar {
      display: flex;
      justify-content: center;
      gap: 16px;
      margin-top: 12px;
      font-size: 12px;
      flex-wrap: wrap;
    }
    .status-badge {
      background: var(--card-bg);
      border: 1px solid var(--border);
      padding: 4px 12px;
      border-radius: 9999px;
      color: var(--accent);
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 20px;
      box-shadow: 0 4px 6px -1px rgba(0,0,0,0.2);
    }
    .card h2 {
      font-size: 18px;
      margin-bottom: 12px;
      color: var(--text);
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .btn {
      display: block;
      width: 100%;
      background: var(--primary);
      color: white;
      border: none;
      padding: 12px 16px;
      border-radius: 6px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.2s;
      margin-top: 10px;
      text-align: center;
    }
    .btn:hover { background: var(--primary-dark); }
    .btn-secondary {
      background: #334155;
    }
    .btn-secondary:hover { background: #475569; }
    .form-group { margin-bottom: 12px; }
    label { display: block; font-size: 12px; color: var(--text-muted); margin-bottom: 4px; }
    input, select {
      width: 100%;
      background: #0f172a;
      border: 1px solid var(--border);
      color: white;
      padding: 8px 12px;
      border-radius: 6px;
      font-size: 14px;
    }
    #results-card {
      display: none;
      margin-top: 24px;
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 20px;
    }
    .spinner {
      display: inline-block;
      width: 16px;
      height: 16px;
      border: 2px solid rgba(255,255,255,0.3);
      border-radius: 50%;
      border-top-color: white;
      animation: spin 0.8s ease-in-out infinite;
      margin-right: 8px;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    pre {
      background: #090d16;
      padding: 14px;
      border-radius: 6px;
      overflow-x: auto;
      font-size: 12px;
      color: #38bdf8;
      margin-top: 12px;
    }
    .strategy-badge {
      display: inline-block;
      padding: 4px 10px;
      border-radius: 6px;
      font-weight: bold;
      font-size: 12px;
      margin-bottom: 12px;
    }
    .starters-table {
      width: 100%;
      border-collapse: collapse;
      margin-top: 14px;
      font-size: 13px;
    }
    .starters-table th, .starters-table td {
      padding: 8px 10px;
      border-bottom: 1px solid var(--border);
      text-align: left;
    }
    .starters-table th { color: var(--text-muted); }
    footer {
      text-align: center;
      margin-top: 40px;
      font-size: 12px;
      color: var(--text-muted);
    }
    footer a { color: var(--accent); text-decoration: none; }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>🏈 Mad Dawg's Command Center</h1>
      <p>AI-Powered Fantasy Football Intelligence • Zero-Cost Cloud Run • Gemini 2.5 Pro</p>
      <div class="status-bar">
        <span class="status-badge">⚡ Status: Operational</span>
        <span class="status-badge">🧠 Brain: Gemini 2.5 Pro</span>
        <span class="status-badge">🏈 Season: 2026</span>
        <span class="status-badge">🌿 Power: us-central1 (Wind)</span>
      </div>
    </header>

    <div class="grid">
      <!-- League 1 Card -->
      <div class="card">
        <h2>🏆 PNA 2026 League</h2>
        <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 14px;">
          12-Team Full PPR • 2 FLEX • 4pt Pass TD • No Kicker
        </p>
        <button class="btn" onclick="fetchEndpoint('/query/start-sit?league_id=991059191', 'PNA 2026 Start Em, Sit Em Report')">
          🎯 Start 'Em, Sit 'Em Master Report
        </button>
        <button class="btn btn-secondary" style="margin-top: 8px;" onclick="fetchEndpoint('/query/propose-trades?league_id=991059191', 'PNA 2026 Winning Trade Proposals')">
          💡 Propose Winning Trades
        </button>
      </div>

      <!-- League 2 Card -->
      <div class="card">
        <h2>🍪 Chips Ahoy</h2>
        <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 14px;">
          10-Team Full PPR • 1 FLEX • 6pt Pass TD • Has Kicker
        </p>
        <button class="btn" onclick="fetchEndpoint('/query/start-sit?league_id=735288', 'Chips Ahoy Start Em, Sit Em Report')">
          🎯 Start 'Em, Sit 'Em Master Report
        </button>
        <button class="btn btn-secondary" style="margin-top: 8px;" onclick="fetchEndpoint('/query/propose-trades?league_id=735288', 'Chips Ahoy Winning Trade Proposals')">
          💡 Propose Winning Trades
        </button>
      </div>

      <!-- Automated Routines -->
      <div class="card">
        <h2>⚡ Routine Triggers</h2>
        <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 14px;">
          Execute weekly scheduled workflows on-demand.
        </p>
        <button class="btn btn-secondary" onclick="fetchEndpoint('/run/weekly', 'Weekly Analysis')">
          📋 Run Weekly Routine (Waivers/TNF)
        </button>
        <button class="btn btn-secondary" style="margin-top: 8px;" onclick="fetchEndpoint('/run/sunday-pregame', 'Sunday Inactives')">
          🚨 Run Sunday Pregame Check
        </button>
      </div>
    </div>

    <!-- Trade Evaluator Card -->
    <div class="card" style="margin-bottom: 24px;">
      <h2>🤝 Instant Trade Evaluator</h2>
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px;">
        <div class="form-group">
          <label>League</label>
          <select id="trade-league">
            <option value="991059191">PNA 2026 (12-Team)</option>
            <option value="735288">Chips Ahoy (10-Team)</option>
          </select>
        </div>
        <div class="form-group">
          <label>Players You Give (comma separated)</label>
          <input type="text" id="trade-give" placeholder="e.g. D'Andre Swift, Tyjae Spears">
        </div>
        <div class="form-group">
          <label>Players You Receive (comma separated)</label>
          <input type="text" id="trade-receive" placeholder="e.g. Jordan Love, Tee Higgins">
        </div>
      </div>
      <button class="btn" style="max-width: 240px;" onclick="submitTrade()">⚖️ Evaluate Trade</button>
    </div>

    <!-- Live Results Section -->
    <div id="results-card">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
        <h2 id="results-title" style="font-size: 20px; color: var(--accent); margin: 0;">Analysis Output</h2>
        <button class="btn-secondary" style="border: none; padding: 4px 10px; border-radius: 4px; font-size: 11px; cursor: pointer;" onclick="document.getElementById('results-card').style.display='none'">Close</button>
      </div>
      <div id="results-content"></div>
    </div>

    <footer>
      <p>Want direct API access? Explore the interactive <a href="/docs" target="_blank">Swagger Documentation (/docs)</a> or query <a href="/health" target="_blank">/health</a>.</p>
    </footer>
  </div>

  <script>
    async function fetchEndpoint(url, title) {
      const resCard = document.getElementById('results-card');
      const resTitle = document.getElementById('results-title');
      const resContent = document.getElementById('results-content');

      resTitle.textContent = title + " — Analyzing...";
      resContent.innerHTML = '<p><span class="spinner"></span> Contacting ESPN, Vegas lines, Open-Meteo, and Gemini Pro...</p>';
      resCard.style.display = 'block';
      resCard.scrollIntoView({ behavior: 'smooth' });

      try {
        const resp = await fetch(url, { method: 'POST' });
        const data = await resp.json();
        resTitle.textContent = title;
        renderOutput(data);
      } catch (err) {
        resContent.innerHTML = '<p style="color: #ef4444;">❌ Error: ' + err.message + '</p>';
      }
    }

    async function submitTrade() {
      const leagueId = parseInt(document.getElementById('trade-league').value);
      const giveRaw = document.getElementById('trade-give').value;
      const receiveRaw = document.getElementById('trade-receive').value;

      if (!giveRaw || !receiveRaw) {
        alert('Please enter players for both sides of the trade.');
        return;
      }

      const giving = giveRaw.split(',').map(s => s.trim()).filter(Boolean);
      const receiving = receiveRaw.split(',').map(s => s.trim()).filter(Boolean);

      const resCard = document.getElementById('results-card');
      const resTitle = document.getElementById('results-title');
      const resContent = document.getElementById('results-content');

      resTitle.textContent = "Evaluating Trade...";
      resContent.innerHTML = '<p><span class="spinner"></span> Running VORP calculations and Gemini Pro analysis...</p>';
      resCard.style.display = 'block';
      resCard.scrollIntoView({ behavior: 'smooth' });

      try {
        const resp = await fetch('/query/trade', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ league_id: leagueId, giving_players: giving, receiving_players: receiving })
        });
        const data = await resp.json();
        resTitle.textContent = "Trade Verdict: " + data.verdict;
        renderOutput(data);
      } catch (err) {
        resContent.innerHTML = '<p style="color: #ef4444;">❌ Error: ' + err.message + '</p>';
      }
    }

    function renderOutput(data) {
      const resContent = document.getElementById('results-content');
      let html = '';

      if (data.vacant_slots && data.vacant_slots.length > 0) {
        html += `<div style="background: rgba(239, 68, 68, 0.2); border-left: 4px solid #ef4444; border-radius: 6px; padding: 14px; margin-bottom: 14px;">
          <h4 style="color: #ef4444; margin-bottom: 6px; font-size: 13px;">🚨 VACANT STARTING SLOTS DETECTED ON ESPN</h4>
          <p style="font-size: 12px; color: #fca5a5; margin-bottom: 6px;">You have starting roster slots that are currently empty. Fill these spots before kickoff:</p>
          <ul style="margin: 0; padding-left: 18px; color: #fecaca; font-size: 12px;">${data.vacant_slots.map(s => `<li>Slot <strong>${s}</strong> is UNFILLED</li>`).join('')}</ul>
        </div>`;
      }

      if (data.actionable_swaps && data.actionable_swaps.length > 0) {
        html += `<div style="background: rgba(234, 179, 8, 0.15); border-left: 4px solid #eab308; border-radius: 6px; padding: 14px; margin-bottom: 14px;">
          <h4 style="color: #eab308; margin-bottom: 6px; font-size: 13px;">⚡ ACTIONABLE ROSTER ADJUSTMENTS NEEDED</h4>
          <ul style="margin: 0; padding-left: 18px; color: #fef08a; font-size: 12px;">${data.actionable_swaps.map(s => `<li>${s}</li>`).join('')}</ul>
        </div>`;
      }

      if (data.game_theory_strategy) {
        html += `<span class="strategy-badge" style="background: #0284c7; color: white;">Strategy: ${data.game_theory_strategy}</span>`;
      }
      if (data.strategy_reasoning) {
        html += `<p style="font-size: 14px; margin-bottom: 12px; color: #cbd5e1;"><em>"${data.strategy_reasoning}"</em></p>`;
      }

      if (data.recommended_starters && data.recommended_starters.length > 0) {
        html += '<h3 style="color: #22c55e; margin-top: 16px; margin-bottom: 8px;">🟢 START EM (Optimal Lineup)</h3>';
        html += '<table class="starters-table"><thead><tr><th>Pos</th><th>Player</th><th>Team</th><th>Proj</th><th>ESPN Status</th><th>Floor-Ceil</th><th>Conf</th><th>Rationale & Game Script</th></tr></thead><tbody>';
        for (const p of data.recommended_starters) {
          let statusBadge = p.current_slot || '-';
          if (p.alignment === 'SWAP_TO_START') {
            statusBadge = `<span style="color: #f59e0b; font-weight: bold; background: rgba(245, 158, 11, 0.15); padding: 2px 6px; border-radius: 4px; font-size: 11px;">⚠️ Bench (Swap In)</span>`;
          } else if (p.alignment === 'ALIGNED') {
            statusBadge = `<span style="color: #22c55e; background: rgba(34, 197, 94, 0.15); padding: 2px 6px; border-radius: 4px; font-size: 11px;">🟢 Started (${p.current_slot})</span>`;
          }
          html += `<tr>
            <td style="font-weight:bold; color: var(--accent);">${p.position}</td>
            <td style="font-weight:bold;">${p.player_name}</td>
            <td>${p.team}</td>
            <td style="color: var(--success); font-weight:bold;">${p.projected_points}</td>
            <td>${statusBadge}</td>
            <td style="font-size: 11px; color: #94a3b8;">${p.floor} - ${p.ceiling}</td>
            <td style="font-size: 11px; color: #38bdf8;">${Math.round(p.confidence * 100)}%</td>
            <td style="font-size: 12px; color: #cbd5e1;">${p.reasoning} ${p.game_script_note ? '<em>(' + p.game_script_note + ')</em>' : ''}</td>
          </tr>`;
        }
        html += '</tbody></table>';
      }

      if (data.bench_players && data.bench_players.length > 0) {
        let injuryAlerts = [];
        let benchRows = '';
        for (const p of data.bench_players) {
          const rUpper = (p.reasoning || '').toUpperCase();
          if (rUpper.includes('OUT') || rUpper.includes('IR') || rUpper.includes('DOUBTFUL') || rUpper.includes('INACTIVE') || rUpper.includes('SUSPENDED')) {
            injuryAlerts.push(`<strong>${p.player_name} (${p.position} - ${p.team})</strong>: ${p.reasoning}`);
          }
          let bStatusBadge = p.current_slot || '-';
          if (p.alignment === 'MOVE_TO_BENCH') {
            bStatusBadge = `<span style="color: #ef4444; font-weight: bold; background: rgba(239, 68, 68, 0.15); padding: 2px 6px; border-radius: 4px; font-size: 11px;">🚨 In Lineup (${p.current_slot}) (Bench Now)</span>`;
          } else if (p.current_slot === 'Bench' || p.current_slot === 'BE') {
            bStatusBadge = `<span style="color: #94a3b8; background: rgba(148, 163, 184, 0.15); padding: 2px 6px; border-radius: 4px; font-size: 11px;">⏸️ On Bench</span>`;
          }
          benchRows += `<tr>
            <td style="font-weight:bold; color: #ef4444;">${p.position}</td>
            <td style="font-weight:bold;">${p.player_name}</td>
            <td>${p.team}</td>
            <td style="color: #94a3b8;">${p.projected_points}</td>
            <td>${bStatusBadge}</td>
            <td style="font-size: 12px; color: #cbd5e1;">${p.reasoning}</td>
          </tr>`;
        }

        if (injuryAlerts.length > 0) {
          html += `<div style="background: rgba(239, 68, 68, 0.15); border-left: 4px solid #ef4444; border-radius: 4px; padding: 12px; margin-top: 16px;">
            <h4 style="color: #ef4444; margin-bottom: 6px; font-size: 13px;">🚨 INJURY / INACTIVE ALERTS (DO NOT START)</h4>
            <ul style="margin: 0; padding-left: 18px; color: #fca5a5; font-size: 12px;">${injuryAlerts.map(a => `<li style="margin-bottom: 4px;">${a}</li>`).join('')}</ul>
          </div>`;
        }

        html += '<h3 style="color: #ef4444; margin-top: 18px; margin-bottom: 8px;">🔴 SIT EM (Bench Options)</h3>';
        html += '<table class="starters-table"><thead><tr><th>Pos</th><th>Player</th><th>Team</th><th>Proj</th><th>ESPN Status</th><th>Why Sit</th></tr></thead><tbody>';
        html += benchRows + '</tbody></table>';
      }

      if (data.key_flex_decisions && data.key_flex_decisions.length > 0) {
        html += `<div style="background: #090d16; border-left: 4px solid #eab308; border-radius: 4px; padding: 12px; margin-top: 16px;">
          <h4 style="color: #eab308; margin-bottom: 8px; font-size: 13px;">⚖️ KEY START/SIT DILEMMAS & FLEX CALLS</h4>
          <ul style="margin: 0; padding-left: 18px; color: #cbd5e1; font-size: 13px;">${data.key_flex_decisions.map(d => `<li style="margin-bottom: 4px;">${d}</li>`).join('')}</ul>
        </div>`;
      }

      if (data.proposals && data.proposals.length > 0) {
        if (data.market_overview) {
          html += `<div style="background: #090d16; padding: 12px 14px; border-radius: 6px; margin-bottom: 14px; border: 1px solid var(--border);"><p style="font-size: 14px; margin: 0; color: #cbd5e1;">📊 <strong>Market Analysis:</strong> <em>"${data.market_overview}"</em></p></div>`;
        }
        html += '<h3 style="color: #38bdf8; margin-top: 16px; margin-bottom: 10px;">💡 PROACTIVE WIN-WIN TRADE PROPOSALS</h3>';
        for (const tp of data.proposals) {
          const giving = (tp.giving_players || []).join(', ');
          const recving = (tp.receiving_players || []).join(', ');
          const mgrStr = tp.target_manager ? ` (${tp.target_manager})` : '';
          html += `<div style="background: #090d16; border-left: 4px solid #38bdf8; border-radius: 6px; padding: 14px; margin-bottom: 12px; border: 1px solid var(--border); border-left-width: 4px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
              <strong style="font-size: 15px; color: #f8fafc;">🤝 Trade with ${tp.target_team_name}${mgrStr}</strong>
              <span style="color: #22c55e; font-weight: bold; font-size: 12px; background: rgba(34, 197, 94, 0.15); padding: 2px 8px; border-radius: 4px;">+${tp.net_vorp_gain > 0 ? tp.net_vorp_gain : 0} Weekly VORP</span>
            </div>
            <p style="margin-bottom: 6px; font-size: 13px;">
              <span style="color: #ef4444; font-weight: bold;">Give:</span> <span style="color: #f8fafc;">${giving}</span>
              <span style="color: #94a3b8; margin: 0 6px;">➔</span>
              <span style="color: #22c55e; font-weight: bold;">Receive:</span> <span style="color: #f8fafc;">${recving}</span>
            </p>
            <p style="font-size: 12px; color: #cbd5e1; margin-bottom: 4px;"><strong style="color: #38bdf8;">Lineup Upgrade:</strong> ${tp.your_lineup_upgrade}</p>
            <p style="font-size: 12px; color: #cbd5e1; margin-bottom: 8px;"><strong style="color: #a78bfa;">Why They Accept:</strong> ${tp.why_target_accepts}</p>
            <div style="background: #1e293b; padding: 10px; border-radius: 4px; font-size: 12px; color: #38bdf8;">
              💬 <strong>Negotiation Pitch:</strong> "${tp.negotiation_pitch}"
            </div>
          </div>`;
        }
      }

      if (data.verdict) {
        const color = data.verdict === 'ACCEPT' ? '#22c55e' : (data.verdict === 'REJECT' ? '#ef4444' : '#eab308');
        html += `<div style="padding: 12px; background: rgba(255,255,255,0.05); border-left: 4px solid ${color}; border-radius: 4px; margin-bottom: 12px;">
          <h3 style="color: ${color}; margin-bottom: 6px;">VERDICT: ${data.verdict} (Net VORP: ${data.your_vorp_change > 0 ? '+' : ''}${data.your_vorp_change})</h3>
          <p style="font-size: 14px; color: #cbd5e1;">${data.reasoning}</p>
          <p style="font-size: 13px; color: #94a3b8; margin-top: 6px;"><strong>Starting Lineup Impact:</strong> ${data.starting_lineup_impact}</p>
          ${data.counter_suggestion ? '<p style="font-size: 13px; color: #eab308; margin-top: 4px;"><strong>Counter Idea:</strong> ' + data.counter_suggestion + '</p>' : ''}
        </div>`;
      }

      html += '<details style="margin-top: 16px;"><summary style="cursor: pointer; color: var(--text-muted); font-size: 12px;">View Raw Technical JSON</summary><pre>' + JSON.stringify(data, null, 2) + '</pre></details>';

      resContent.innerHTML = html;
    }
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def serve_dashboard() -> str:
    """Serve the interactive Web Command Center for on-demand analysis."""
    return DASHBOARD_HTML


@app.get("/health")
def health_check() -> dict[str, Any]:
    """Health check endpoint confirming service status and configuration."""
    return {
        "status": "healthy",
        "season": get_current_season(),
        "model": get_gemini_model(),
        "leagues": [
            {
                "id": cfg.league_id,
                "name": cfg.name,
                "teams": cfg.num_teams,
                "pass_td": cfg.scoring.pass_td,
            }
            for cfg in ALL_LEAGUES.values()
        ],
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/run/weekly")
def run_weekly_analysis() -> dict[str, Any]:
    """Automated weekday workflow triggered by Cloud Scheduler (Tue, Thu, Fri, Sat).
    Routes internally based on current weekday (America/New_York).
    """
    import zoneinfo

    eastern = zoneinfo.ZoneInfo("America/New_York")
    now_et = datetime.now(eastern)
    weekday = now_et.weekday()  # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
    day_name = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][
        weekday
    ]

    logger.info(f"Running automated weekly job for {day_name}...")

    results = {}
    client = None
    try:
        client = GeminiIntelligenceClient()
    except Exception as e:
        logger.warning(
            f"Could not initialize GeminiIntelligenceClient: {e}. Falling back to deterministic mode."
        )

    for league_id, league_config in ALL_LEAGUES.items():
        try:
            espn = LeagueClient().get_league(league_config)
            current_week = get_current_week(espn)
            my_team = next((t for t in espn.teams if t.team_id == league_config.team_id), None)
            if not my_team:
                continue

            parsed_roster = parse_roster(my_team, league_config)
            matchup = get_weekly_matchup(espn, league_config.team_id, current_week, league_config)

            if weekday == 1:  # Tuesday: Waiver Wire Analysis
                free_agents = [
                    {
                        "name": p.name,
                        "position": p.position,
                        "team": p.proTeam,
                        "projected_points": getattr(p, "projected_points", 0.0),
                        "percent_owned": getattr(p, "percent_owned", 0.0),
                    }
                    for p in espn.free_agents(size=25)
                ]
                trending = fetch_trending_adds(lookback_hours=24, limit=20)
                report = evaluate_waivers(
                    league_config,
                    current_week,
                    parsed_roster,
                    free_agents,
                    trending_adds=trending,
                    client=client,
                )
                results[league_config.short_name] = report.model_dump()

            elif weekday in (3, 4):  # Thursday/Friday: Injury & TNF Check
                roster_names = [p.name for p in parsed_roster.players]
                injuries = get_injury_report(roster_names)
                odds = fetch_week_odds()
                lineup = optimize_lineup(
                    league_config,
                    current_week,
                    parsed_roster,
                    matchup=matchup,
                    injuries=injuries,
                    odds=odds,
                    client=client,
                )
                results[league_config.short_name] = lineup.model_dump()

            elif weekday == 5:  # Saturday: Full Matchup Preview & Scouting
                odds = fetch_week_odds()
                if matchup:
                    report = generate_matchup_preview(
                        league_config, current_week, matchup, odds=odds, client=client
                    )
                    results[league_config.short_name] = report.model_dump()

            else:
                results[league_config.short_name] = {
                    "message": f"No specific routine scheduled for {day_name}"
                }

        except Exception as e:
            logger.error(f"Error processing league {league_id}: {e}", exc_info=True)
            results[league_config.short_name] = {"error": str(e)}

    # Send digest email
    send_digest_email("weekly_analysis", results, day=day_name)

    return {
        "job": "weekly_analysis",
        "day": day_name,
        "results": results,
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/run/sunday-pregame")
def run_sunday_pregame() -> dict[str, Any]:
    """Sunday 90-minute pregame alert: checks active/inactive statuses, weather, and finalizes starters."""
    logger.info("Running Sunday pregame inactive & final lineup optimization...")

    results = {}
    client = None
    try:
        client = GeminiIntelligenceClient()
    except Exception as e:
        logger.warning(
            f"Could not initialize GeminiIntelligenceClient: {e}. Falling back to deterministic mode."
        )

    odds = fetch_week_odds()

    for league_id, league_config in ALL_LEAGUES.items():
        try:
            espn = LeagueClient().get_league(league_config)
            current_week = get_current_week(espn)
            my_team = next((t for t in espn.teams if t.team_id == league_config.team_id), None)
            if not my_team:
                continue

            parsed_roster = parse_roster(my_team, league_config)
            matchup = get_weekly_matchup(espn, league_config.team_id, current_week, league_config)

            # Live injury and weather check for roster
            roster_names = [p.name for p in parsed_roster.players]
            injuries = get_injury_report(roster_names)

            weather_map = {}
            for p in parsed_roster.starters:
                if p.team and p.team not in weather_map:
                    weather_map[p.team] = fetch_game_weather(p.team, datetime.now())

            lineup = optimize_lineup(
                league=league_config,
                week=current_week,
                roster=parsed_roster,
                matchup=matchup,
                injuries=injuries,
                odds=odds,
                weather_map=weather_map,
                client=client,
            )
            results[league_config.short_name] = lineup.model_dump()

        except Exception as e:
            logger.error(f"Error in Sunday pregame for league {league_id}: {e}", exc_info=True)
            results[league_config.short_name] = {"error": str(e)}

    # Send game-day digest email
    send_digest_email("sunday_pregame", results)

    return {
        "job": "sunday_pregame",
        "results": results,
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/query/start-sit")
@app.post("/query/lineup")
def query_lineup(league_id: int = Query(..., description="ESPN League ID")) -> dict[str, Any]:
    """On-demand Start 'Em, Sit 'Em master report for a specific league."""
    league_config = ALL_LEAGUES.get(league_id)
    if not league_config:
        raise HTTPException(
            status_code=404, detail=f"League {league_id} not found in configuration."
        )

    try:
        espn = LeagueClient().get_league(league_config)
        current_week = get_current_week(espn)
        my_team = next((t for t in espn.teams if t.team_id == league_config.team_id), None)
        if not my_team:
            raise HTTPException(
                status_code=404, detail=f"Team {league_config.team_id} not found in league."
            )

        parsed_roster = parse_roster(my_team, league_config)
        matchup = get_weekly_matchup(espn, league_config.team_id, current_week, league_config)
        roster_names = [p.name for p in parsed_roster.players]
        injuries = get_injury_report(roster_names)
        odds = fetch_week_odds()

        client = None
        try:
            client = GeminiIntelligenceClient()
        except Exception:
            pass

        lineup = optimize_lineup(
            league=league_config,
            week=current_week,
            roster=parsed_roster,
            matchup=matchup,
            injuries=injuries,
            odds=odds,
            client=client,
        )
        return lineup.model_dump()
    except Exception as e:
        logger.error(f"Error querying lineup: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/query/trade")
def query_trade(req: TradeRequest) -> dict[str, Any]:
    """On-demand trade evaluation."""
    league_config = ALL_LEAGUES.get(req.league_id)
    if not league_config:
        raise HTTPException(status_code=404, detail=f"League {req.league_id} not found.")

    try:
        espn = LeagueClient().get_league(league_config)
        my_team = next((t for t in espn.teams if t.team_id == league_config.team_id), None)
        parsed_roster = parse_roster(my_team, league_config) if my_team else None

        client = None
        try:
            client = GeminiIntelligenceClient()
        except Exception:
            pass

        verdict = evaluate_trade(
            league=league_config,
            roster=parsed_roster,
            giving_players=req.giving_players,
            receiving_players=req.receiving_players,
            client=client,
        )
        return verdict.model_dump()
    except Exception as e:
        logger.error(f"Error evaluating trade: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/query/propose-trades")
def query_propose_trades(league_id: int = Query(..., description="ESPN League ID")) -> dict[str, Any]:
    """On-demand proactive trade proposals scanning all teams across the league."""
    league_config = ALL_LEAGUES.get(league_id)
    if not league_config:
        raise HTTPException(
            status_code=404, detail=f"League {league_id} not found in configuration."
        )

    try:
        espn = LeagueClient().get_league(league_config)
        current_week = get_current_week(espn)

        client = None
        try:
            client = GeminiIntelligenceClient()
        except Exception:
            pass

        report = propose_league_trades(
            league=league_config,
            week=current_week,
            espn_league=espn,
            client=client,
        )
        return report.model_dump()
    except Exception as e:
        logger.error(f"Error proposing trades for league {league_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

