"""
FastAPI application serving the NFL Fantasy Football Agent on Google Cloud Run.
Exposes endpoints for Cloud Scheduler cron triggers and on-demand analysis queries.
"""

import concurrent.futures
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from src.analysis.lineup import optimize_lineup
from src.analysis.matchup_preview import generate_matchup_preview
from src.analysis.recap import generate_weekly_recap
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
from src.intelligence.gemini_client import (
    PREFERRED_MODELS,
    GeminiIntelligenceClient,
    negotiate_active_model,
)
from src.notifications.email import send_digest_email, send_error_alert_email

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("fantasy_agent")

_gemini_health_cache: dict[str, Any] = {
    "status": "untested",
    "last_checked": None,
    "error": None,
}


def check_gemini_connectivity(force: bool = False) -> dict[str, Any]:
    """Check Gemini model connectivity and cache the result."""
    global _gemini_health_cache
    if not force and _gemini_health_cache["last_checked"] is not None:
        return _gemini_health_cache

    try:
        client = GeminiIntelligenceClient()
        if client.validate_model():
            _gemini_health_cache = {
                "status": "connected",
                "model": client.model,
                "last_checked": datetime.now().isoformat(),
                "error": None,
            }
            logger.info("Gemini connectivity verified: %s reachable", client.model)
        else:
            _gemini_health_cache = {
                "status": "unreachable",
                "model": client.model,
                "last_checked": datetime.now().isoformat(),
                "error": "Model validation returned False",
            }
            logger.warning(
                "Gemini connectivity check failed: %s validation failed. Fallback active.",
                client.model,
            )
    except Exception as e:
        _gemini_health_cache = {
            "status": "unreachable",
            "model": get_gemini_model(),
            "last_checked": datetime.now().isoformat(),
            "error": str(e),
        }
        logger.warning("Gemini connectivity check error: %s. Fallback active.", e)

    return _gemini_health_cache


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run diagnostics and model negotiation on app startup."""
    active_model, diag = negotiate_active_model(force=True)
    logger.info(
        "Startup model negotiation: active=%s (target=%s, ready=%s)",
        active_model,
        PREFERRED_MODELS[0],
        diag.get("auto_upgrade_active"),
    )
    check_gemini_connectivity(force=True)
    yield


app = FastAPI(
    title="NFL Fantasy Football Agent",
    description="AI-powered Fantasy Football veteran analyst for ESPN leagues, powered by Gemini Pro.",
    version="1.0.0",
    lifespan=lifespan,
)


class TradeRequest(BaseModel):
    league_id: int
    giving_players: list[str]
    receiving_players: list[str]


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, viewport-fit=cover">
  <title>Mad Dawg's Fantasy Football Command Center</title>
  <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><circle cx=%2250%22 cy=%2250%22 r=%2248%22 fill=%22%230b0f19%22 stroke=%22%23d9381e%22 stroke-width=%224%22/><text x=%2250%25%22 y=%2254%25%22 font-size=%2252%22 text-anchor=%22middle%22 dominant-baseline=%22central%22>🏈</text></svg>">
  <link rel="alternate icon" href="/favicon.ico">
  <link rel="apple-touch-icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><circle cx=%2250%22 cy=%2250%22 r=%2248%22 fill=%22%230b0f19%22 stroke=%22%23d9381e%22 stroke-width=%224%22/><text x=%2250%25%22 y=%2254%25%22 font-size=%2252%22 text-anchor=%22middle%22 dominant-baseline=%22central%22>🏈</text></svg>">
  <style>
    :root {
      --primary: #d9381e;
      --primary-dark: #b72b15;
      --bg: #0b0f19;
      --card-bg: #151e32;
      --card-subtle: #1e293b;
      --border: #283548;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --success: #22c55e;
      --success-bg: rgba(34, 197, 94, 0.15);
      --success-border: #16a34a;
      --warning: #f59e0b;
      --warning-bg: rgba(245, 158, 11, 0.15);
      --warning-border: #d97706;
      --danger: #ef4444;
      --danger-bg: rgba(239, 68, 68, 0.18);
      --danger-border: #dc2626;
      --accent: #38bdf8;
      --accent-dark: #0284c7;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      background-color: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding: 16px 12px;
    }
    .container { max-width: 920px; margin: 0 auto; }
    header {
      text-align: center;
      margin-bottom: 22px;
      padding-bottom: 18px;
      border-bottom: 1px solid var(--border);
    }
    header h1 { font-size: 24px; color: var(--primary); margin-bottom: 6px; letter-spacing: -0.5px; }
    header p { color: var(--text-muted); font-size: 13px; }
    .status-bar {
      display: flex;
      justify-content: center;
      gap: 10px;
      margin-top: 12px;
      font-size: 11px;
      flex-wrap: wrap;
    }
    .status-badge {
      background: var(--card-bg);
      border: 1px solid var(--border);
      padding: 4px 10px;
      border-radius: 9999px;
      color: var(--accent);
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 14px;
      margin-bottom: 20px;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 18px;
      box-shadow: 0 4px 10px -2px rgba(0,0,0,0.3);
    }
    .card h2 {
      font-size: 17px;
      margin-bottom: 10px;
      color: var(--text);
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .btn {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 100%;
      min-height: 48px;
      background: var(--primary);
      color: white;
      border: none;
      padding: 12px 16px;
      border-radius: 8px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s ease;
      margin-top: 10px;
      text-align: center;
    }
    .btn:hover, .btn:active { background: var(--primary-dark); }
    .btn-secondary {
      background: #334155;
    }
    .btn-secondary:hover, .btn-secondary:active { background: #475569; }
    .form-group { margin-bottom: 12px; }
    label { display: block; font-size: 13px; color: var(--text-muted); margin-bottom: 6px; }
    input, select {
      width: 100%;
      min-height: 48px;
      background: #090d16;
      border: 1px solid var(--border);
      color: white;
      padding: 10px 14px;
      border-radius: 8px;
      font-size: 16px; /* Prevents auto-zoom on iOS */
    }
    input:focus, select:focus {
      outline: none;
      border-color: var(--accent);
    }
    #results-card {
      display: none;
      margin-top: 20px;
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 18px;
    }
    .spinner {
      display: inline-block;
      width: 18px;
      height: 18px;
      border: 2px solid rgba(255,255,255,0.3);
      border-radius: 50%;
      border-top-color: white;
      animation: spin 0.8s ease-in-out infinite;
      margin-right: 8px;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
    pre {
      background: #090d16;
      padding: 12px;
      border-radius: 8px;
      overflow-x: auto;
      font-size: 11px;
      color: #38bdf8;
      margin-top: 10px;
    }
    .strategy-badge {
      display: inline-block;
      padding: 4px 10px;
      border-radius: 6px;
      font-weight: bold;
      font-size: 12px;
    }

    /* Mobile-first Player Cards */
    .player-card {
      background: var(--card-subtle);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px 14px;
      margin-bottom: 10px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .card-keep {
      border-left: 5px solid var(--success);
    }
    .card-bench-now {
      border-left: 5px solid var(--danger);
      background: rgba(239, 68, 68, 0.08);
    }
    .card-promote {
      border-left: 5px solid var(--warning);
      background: rgba(245, 158, 11, 0.08);
    }
    .card-stay-bench {
      border-left: 5px solid #64748b;
    }
    .player-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .player-info {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .slot-badge {
      font-size: 11px;
      font-weight: 700;
      padding: 3px 7px;
      border-radius: 4px;
      background: #090d16;
      color: var(--accent);
      border: 1px solid #334155;
    }
    .player-name {
      font-weight: 700;
      font-size: 15px;
      color: var(--text);
    }
    .player-meta {
      font-size: 12px;
      color: var(--text-muted);
    }
    .matchup-badge {
      font-size: 11px;
      font-weight: 600;
      padding: 2px 7px;
      border-radius: 4px;
      background: rgba(56, 189, 248, 0.12);
      color: #38bdf8;
      border: 1px solid rgba(56, 189, 248, 0.28);
      display: inline-flex;
      align-items: center;
      gap: 3px;
      line-height: 1.3;
    }
    .matchup-badge.bye {
      background: rgba(148, 163, 184, 0.12);
      color: #94a3b8;
      border-color: rgba(148, 163, 184, 0.28);
    }
    .player-proj {
      font-size: 14px;
      font-weight: 700;
      color: #38bdf8;
    }
    .action-badge {
      font-size: 11px;
      font-weight: 700;
      padding: 4px 9px;
      border-radius: 6px;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      white-space: nowrap;
    }
    .badge-keep {
      background: var(--success-bg);
      color: #86efac;
      border: 1px solid var(--success-border);
    }
    .badge-bench-now {
      background: var(--danger-bg);
      color: #fca5a5;
      border: 1px solid var(--danger-border);
    }
    .badge-promote {
      background: var(--warning-bg);
      color: #fde047;
      border: 1px solid var(--warning-border);
    }
    .badge-stay-bench {
      background: rgba(100, 116, 139, 0.2);
      color: #cbd5e1;
      border: 1px solid #475569;
    }
    .action-detail {
      font-size: 13px;
      color: #cbd5e1;
      line-height: 1.4;
    }
    .player-stats {
      font-size: 11px;
      color: var(--text-muted);
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .alert-box {
      border-radius: 10px;
      padding: 14px 16px;
      margin-bottom: 14px;
    }
    .alert-danger {
      background: var(--danger-bg);
      border-left: 5px solid var(--danger);
      color: #fecaca;
    }
    .alert-warning {
      background: var(--warning-bg);
      border-left: 5px solid var(--warning);
      color: #fef08a;
    }
    .alert-success {
      background: var(--success-bg);
      border-left: 5px solid var(--success);
      color: #bbf7d0;
    }
    .trade-card {
      background: var(--card-subtle);
      border: 1px solid var(--border);
      border-left: 5px solid var(--accent);
      border-radius: 10px;
      padding: 14px;
      margin-bottom: 12px;
    }
    .trade-actions {
      margin-top: 10px;
      display: flex;
      justify-content: flex-end;
    }
    .btn-copy {
      min-height: 44px;
      padding: 8px 14px;
      font-size: 13px;
      font-weight: 600;
      border-radius: 6px;
      background: #090d16;
      color: var(--accent);
      border: 1px solid var(--accent-dark);
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: all 0.2s ease;
    }
    .btn-copy:hover, .btn-copy:active {
      background: var(--accent-dark);
      color: white;
    }

    @media (max-width: 640px) {
      body { padding: 12px 8px; }
      header h1 { font-size: 20px; }
      header p { font-size: 12px; }
      .card { padding: 14px; }
      .player-header { flex-direction: column; align-items: flex-start; gap: 4px; }
      .action-badge { align-self: flex-start; }
    }
    footer {
      text-align: center;
      margin-top: 36px;
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
      <p>AI Fantasy Intelligence • Zero-Cost Cloud Run • Gemini Reasoning</p>
      <div class="status-bar">
        <span id="system-health-badge" class="status-badge" style="cursor: pointer; transition: all 0.2s;" onclick="checkSystemHealth()" title="Click to re-verify live system health">⚡ Status: Checking...</span>
        <span id="brain-badge" class="status-badge">🧠 Brain: Gemini Pro (Probing...)</span>
        <span class="status-badge">🏈 Season: 2026</span>
        <span class="status-badge">🌿 Region: us-central1</span>
      </div>
    </header>

    <div class="grid">
      <!-- League 1 Card -->
      <div class="card">
        <h2>🏆 PNA 2026 League</h2>
        <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 12px;">
          12-Team Full PPR • 2 FLEX • 4pt Pass TD • No Kicker
        </p>
        <button class="btn" onclick="fetchEndpoint('/query/start-sit?league_id=991059191', 'PNA 2026 Start Em, Sit Em Report')">
          🎯 Start 'Em, Sit 'Em Master Report
        </button>
        <button class="btn btn-secondary" onclick="fetchEndpoint('/query/weekly-recap?league_id=991059191', 'PNA 2026 Film Room & Weekly Recap')">
          🎬 Weekly Recap (Film Room)
        </button>
        <button class="btn btn-secondary" onclick="fetchEndpoint('/query/waivers?league_id=991059191', 'PNA 2026 Waiver Wire Intel')">
          🔄 Waiver Wire Intel
        </button>
        <button class="btn btn-secondary" onclick="fetchEndpoint('/query/propose-trades?league_id=991059191', 'PNA 2026 Winning Trade Proposals')">
          💡 Propose Winning Trades
        </button>
      </div>

      <!-- League 2 Card -->
      <div class="card">
        <h2>🍪 Chips Ahoy</h2>
        <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 12px;">
          10-Team Full PPR • 1 FLEX • 6pt Pass TD • Has Kicker
        </p>
        <button class="btn" onclick="fetchEndpoint('/query/start-sit?league_id=735288', 'Chips Ahoy Start Em, Sit Em Report')">
          🎯 Start 'Em, Sit 'Em Master Report
        </button>
        <button class="btn btn-secondary" onclick="fetchEndpoint('/query/weekly-recap?league_id=735288', 'Chips Ahoy Film Room & Weekly Recap')">
          🎬 Weekly Recap (Film Room)
        </button>
        <button class="btn btn-secondary" onclick="fetchEndpoint('/query/waivers?league_id=735288', 'Chips Ahoy Waiver Wire Intel')">
          🔄 Waiver Wire Intel
        </button>
        <button class="btn btn-secondary" onclick="fetchEndpoint('/query/propose-trades?league_id=735288', 'Chips Ahoy Winning Trade Proposals')">
          💡 Propose Winning Trades
        </button>
      </div>

      <!-- Automated Routines -->
      <div class="card">
        <h2>⚡ Routine Triggers</h2>
        <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 12px;">
          Execute weekly scheduled workflows on-demand.
        </p>
        <button class="btn btn-secondary" onclick="fetchEndpoint('/run/weekly', 'Weekly Analysis')">
          📋 Run Weekly Routine (Waivers/TNF)
        </button>
        <button class="btn btn-secondary" onclick="fetchEndpoint('/run/sunday-pregame', 'Sunday Inactives')">
          🚨 Run Sunday Pregame Check
        </button>
      </div>
    </div>

    <!-- Trade Evaluator Card -->
    <div class="card" style="margin-bottom: 20px;">
      <h2>🤝 Instant Trade Evaluator</h2>
      <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 12px;">
        Evaluates proposed trades against your <strong>exact roster & starting lineup</strong>, preventing unowned/duplicate player errors and revealing true net weekly starting points impact.
      </p>
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px;">
        <div class="form-group">
          <label>League</label>
          <select id="trade-league" onchange="loadTradeRoster()">
            <option value="991059191">PNA 2026 (12-Team)</option>
            <option value="735288">Chips Ahoy (10-Team)</option>
          </select>
        </div>
        <div class="form-group">
          <label>Players You Give (must be on your roster)</label>
          <input type="text" id="trade-give" placeholder="e.g. D'Andre Swift, Tyjae Spears">
          <div id="trade-give-roster-tags" style="margin-top: 6px; display: flex; flex-wrap: wrap; gap: 4px;"></div>
        </div>
        <div class="form-group">
          <label>Players You Receive (comma separated)</label>
          <input type="text" id="trade-receive" placeholder="e.g. Jordan Love, Tee Higgins">
        </div>
      </div>
      <button class="btn" style="max-width: 240px; margin-top: 6px;" onclick="submitTrade()">⚖️ Evaluate Trade</button>
    </div>

    <!-- Live Results Section -->
    <div id="results-card">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
        <h2 id="results-title" style="font-size: 18px; color: var(--accent); margin: 0;">Analysis Output</h2>
        <button class="btn-secondary" style="border: none; padding: 6px 12px; border-radius: 6px; font-size: 12px; cursor: pointer;" onclick="document.getElementById('results-card').style.display='none'">Close</button>
      </div>
      <div id="results-content"></div>
    </div>

    <footer>
      <p>API Access: <a href="/docs" target="_blank">Swagger Documentation (/docs)</a> • <a href="/health" target="_blank">Health Status (/health)</a> • <a href="/health/models" target="_blank">Model Diagnostics (/health/models)</a></p>
    </footer>
  </div>

  <script>
    let activeTimer = null;

    async function fetchEndpoint(url, title) {
      const resCard = document.getElementById('results-card');
      const resTitle = document.getElementById('results-title');
      const resContent = document.getElementById('results-content');

      if (activeTimer) clearInterval(activeTimer);
      let elapsedSec = 0;

      function updateLoadingStatus() {
        let stageText = "Syncing live ESPN rosters and scoring rules...";
        if (elapsedSec > 4 && elapsedSec <= 12) {
          stageText = "Pulling live Vegas spreads, game totals, and weather conditions...";
        } else if (elapsedSec > 12 && elapsedSec <= 25) {
          stageText = "Cross-referencing Sleeper trending pickups and injury designations...";
        } else if (elapsedSec > 25) {
          stageText = "Synthesizing deep game-theory decisions via Gemini Pro (crunching multi-slot optimization)...";
        }
        resContent.innerHTML = `
          <div style="background: rgba(56, 189, 248, 0.05); border: 1px solid rgba(56, 189, 248, 0.25); border-radius: 8px; padding: 18px;">
            <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px;">
              <span class="spinner"></span>
              <strong style="color: var(--accent); font-size: 15px;">Analysis in Progress (${elapsedSec}s elapsed)...</strong>
            </div>
            <p style="color: #cbd5e1; font-size: 13px; margin: 0;">${stageText}</p>
          </div>
        `;
      }

      resTitle.textContent = title + " — Analyzing...";
      updateLoadingStatus();
      resCard.style.display = 'block';
      resCard.scrollIntoView({ behavior: 'smooth' });

      activeTimer = setInterval(() => {
        elapsedSec++;
        updateLoadingStatus();
      }, 1000);

      try {
        const resp = await fetch(url, { method: 'POST' });
        clearInterval(activeTimer);
        const data = await resp.json();

        if (!resp.ok || data.detail || data.error) {
          const errMsg = data.detail || data.error || ('HTTP ' + resp.status + ': ' + resp.statusText);
          resTitle.textContent = title + " — ⚠️ Issue Detected";
          resContent.innerHTML = `
            <div style="background: rgba(239, 68, 68, 0.15); border: 2px solid #ef4444; border-radius: 8px; padding: 18px; margin: 12px 0;">
              <h3 style="color: #ef4444; margin: 0 0 8px 0; display: flex; align-items: center; gap: 8px;">
                <span>🚨</span> Analysis Warning / Error
              </h3>
              <p style="color: #fca5a5; font-size: 14px; margin: 0 0 10px 0; line-height: 1.5; font-family: monospace;">${errMsg}</p>
              <p style="font-size: 12px; color: #94a3b8; margin: 0;">The agent prevented this error from failing silently. Check your ESPN connection and credentials if the problem persists.</p>
            </div>
          `;
          return;
        }

        resTitle.textContent = title;
        renderOutput(data);
      } catch (err) {
        clearInterval(activeTimer);
        resTitle.textContent = title + " — ⚠️ Request Error";
        resContent.innerHTML = `
          <div style="background: rgba(239, 68, 68, 0.15); border: 2px solid #ef4444; border-radius: 8px; padding: 18px; margin: 12px 0;">
            <h3 style="color: #ef4444; margin: 0 0 8px 0;">🚨 Network Error</h3>
            <p style="color: #fca5a5; font-size: 14px; margin: 0;">${err.message}</p>
          </div>
        `;
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

      if (activeTimer) clearInterval(activeTimer);
      let elapsedSec = 0;

      resTitle.textContent = "Evaluating Trade...";
      resContent.innerHTML = `
        <div style="background: rgba(56, 189, 248, 0.05); border: 1px solid rgba(56, 189, 248, 0.25); border-radius: 8px; padding: 18px;">
          <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px;">
            <span class="spinner"></span>
            <strong style="color: var(--accent); font-size: 15px;">Running VORP Analysis with Gemini Pro (0s elapsed)...</strong>
          </div>
          <p style="color: #cbd5e1; font-size: 13px; margin: 0;">Verifying roster ownership and calculating net starting points delta...</p>
        </div>
      `;
      resCard.style.display = 'block';
      resCard.scrollIntoView({ behavior: 'smooth' });

      activeTimer = setInterval(() => {
        elapsedSec++;
        resContent.innerHTML = `
          <div style="background: rgba(56, 189, 248, 0.05); border: 1px solid rgba(56, 189, 248, 0.25); border-radius: 8px; padding: 18px;">
            <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px;">
              <span class="spinner"></span>
              <strong style="color: var(--accent); font-size: 15px;">Running VORP Analysis with Gemini Pro (${elapsedSec}s elapsed)...</strong>
            </div>
            <p style="color: #cbd5e1; font-size: 13px; margin: 0;">Verifying roster ownership and calculating net starting points delta...</p>
          </div>
        `;
      }, 1000);

      try {
        const resp = await fetch('/query/trade', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ league_id: leagueId, giving_players: giving, receiving_players: receiving })
        });
        clearInterval(activeTimer);
        const data = await resp.json();

        if (!resp.ok || data.detail || (data.error && !data.verdict)) {
          const errMsg = data.detail || data.error || ('HTTP ' + resp.status + ': ' + resp.statusText);
          resTitle.textContent = "Trade Evaluation Failed";
          resContent.innerHTML = `
            <div style="background: rgba(239, 68, 68, 0.15); border: 2px solid #ef4444; border-radius: 8px; padding: 18px; margin: 12px 0;">
              <h3 style="color: #ef4444; margin: 0 0 8px 0;">🚨 Trade Evaluation Error</h3>
              <p style="color: #fca5a5; font-size: 14px; margin: 0;">${errMsg}</p>
            </div>
          `;
          return;
        }

        resTitle.textContent = "Trade Verdict: " + (data.verdict || 'Evaluation Complete');
        renderOutput(data);
      } catch (err) {
        clearInterval(activeTimer);
        resTitle.textContent = "Trade Evaluation Failed";
        resContent.innerHTML = '<p style="color: #ef4444;">❌ Error: ' + err.message + '</p>';
      }
    }

    async function loadTradeRoster() {
      const leagueSelect = document.getElementById('trade-league');
      const tagContainer = document.getElementById('trade-give-roster-tags');
      if (!leagueSelect || !tagContainer) return;
      const leagueId = leagueSelect.value;
      tagContainer.innerHTML = '<span style="font-size: 11px; color: var(--text-muted);">Loading your roster...</span>';
      try {
        const resp = await fetch(`/query/roster-players?league_id=${leagueId}`);
        const data = await resp.json();
        if (data.players && data.players.length > 0) {
          tagContainer.innerHTML = '<span style="font-size: 11px; color: var(--text-muted); width: 100%; margin-bottom: 3px;">Your Roster (click to add/remove):</span>';
          data.players.forEach(p => {
            const pill = document.createElement('button');
            pill.type = 'button';
            pill.style.cssText = 'background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.15); color: #cbd5e1; padding: 2px 7px; border-radius: 12px; font-size: 11px; cursor: pointer; transition: all 0.15s; margin-right: 4px; margin-bottom: 4px;';
            pill.textContent = `${p.name} (${p.pos})`;
            pill.onmouseover = () => { pill.style.borderColor = 'var(--accent)'; };
            pill.onmouseout = () => { pill.style.borderColor = 'rgba(255,255,255,0.15)'; };
            pill.onclick = () => togglePlayerToGive(p.name);
            tagContainer.appendChild(pill);
          });
        } else {
          tagContainer.innerHTML = '';
        }
      } catch (e) {
        tagContainer.innerHTML = '';
      }
    }

    function togglePlayerToGive(playerName) {
      const input = document.getElementById('trade-give');
      let current = input.value.split(',').map(s => s.trim()).filter(Boolean);
      const idx = current.findIndex(n => n.toLowerCase() === playerName.toLowerCase());
      if (idx >= 0) {
        current.splice(idx, 1);
      } else {
        current.push(playerName);
      }
      input.value = current.join(', ');
    }

    async function checkSystemHealth() {
      const badge = document.getElementById('system-health-badge');
      if (!badge) return;
      badge.innerHTML = '⚡ Checking Systems...';
      badge.style.borderColor = 'var(--border)';
      badge.style.color = 'var(--text-muted)';
      try {
        const resp = await fetch('/health');
        const data = await resp.json();
        if (data.status === 'healthy' && data.gemini_status === 'connected') {
          badge.innerHTML = '🟢 All Systems Operational (ESPN & AI Ready)';
          badge.style.borderColor = '#22c55e';
          badge.style.color = '#86efac';
          badge.style.background = 'rgba(34, 197, 94, 0.12)';
        } else {
          badge.innerHTML = `⚠️ Systems Degraded: ${data.gemini_status || 'Issues detected'}`;
          badge.style.borderColor = '#f59e0b';
          badge.style.color = '#fde047';
          badge.style.background = 'rgba(245, 158, 11, 0.15)';
        }
      } catch (err) {
        badge.innerHTML = '🔴 Systems Offline / Unreachable';
        badge.style.borderColor = '#ef4444';
        badge.style.color = '#fca5a5';
        badge.style.background = 'rgba(239, 68, 68, 0.15)';
      }
    }

    async function updateBrainBadge() {
      try {
        const resp = await fetch('/health/models');
        const d = await resp.json();
        const badge = document.getElementById('brain-badge');
        if (badge && d.active_model) {
          const isTarget = d.auto_upgrade_active;
          badge.innerHTML = `🧠 Brain: ${d.active_model} ${isTarget ? '🚀' : '(Target: ' + d.target_model + ')'}`;
          if (isTarget) {
            badge.style.borderColor = '#22c55e';
            badge.style.color = '#86efac';
          }
        }
      } catch (e) {}
    }

    window.addEventListener('DOMContentLoaded', () => {
      checkSystemHealth();
      loadTradeRoster();
      updateBrainBadge();
    });

    function copyPitch(text, btnId) {
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(() => showCopied(btnId)).catch(() => fallbackCopy(text, btnId));
      } else {
        fallbackCopy(text, btnId);
      }
    }

    function fallbackCopy(text, btnId) {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand('copy');
        showCopied(btnId);
      } catch (e) {
        alert('Copied: ' + text);
      }
      document.body.removeChild(ta);
    }

    function showCopied(btnId) {
      const btn = document.getElementById(btnId);
      if (btn) {
        const orig = btn.innerHTML;
        btn.innerHTML = '✅ Copied!';
        btn.style.borderColor = '#22c55e';
        btn.style.color = '#86efac';
        setTimeout(() => {
          btn.innerHTML = orig;
          btn.style.borderColor = '';
          btn.style.color = '';
        }, 2200);
      }
    }

    function renderOutput(data) {
      const resContent = document.getElementById('results-content');
      let html = '';

      // Prominent Date / Time Stamp Banner
      const now = new Date();
      const localTimeStr = now.toLocaleDateString('en-US', {
        weekday: 'long',
        month: 'short',
        day: 'numeric',
        year: 'numeric'
      }) + ' at ' + now.toLocaleTimeString('en-US', {
        hour: 'numeric',
        minute: '2-digit',
        second: '2-digit',
        hour12: true,
        timeZoneName: 'short'
      });
      const displayTimestamp = data.generated_at || localTimeStr;

      html += `<div style="display: flex; justify-content: space-between; align-items: center; background: #090d16; border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; margin-bottom: 16px; flex-wrap: wrap; gap: 8px;">
        <span style="font-size: 13px; color: var(--accent); font-weight: 600; display: flex; align-items: center; gap: 6px;">
          🕒 Report Generated: <strong>${displayTimestamp}</strong>
        </span>
        <span style="font-size: 11px; color: var(--text-muted); background: #1e293b; padding: 3px 10px; border-radius: 9999px; border: 1px solid #334155;">
          ● Live Data (ESPN & Vegas)
        </span>
      </div>`;

      // Top-level Error or Alert Banner
      if (data.error || data.detail) {
        const msg = data.error || data.detail;
        html += `<div style="background: rgba(239, 68, 68, 0.15); border: 2px solid #ef4444; border-radius: 8px; padding: 18px; margin-bottom: 16px;">
          <h3 style="color: #ef4444; margin: 0 0 8px 0; display: flex; align-items: center; gap: 8px;">
            <span>🚨</span> Routine Issue / Alert
          </h3>
          <p style="color: #fca5a5; font-size: 14px; margin: 0; line-height: 1.4; word-break: break-word;">${typeof msg === 'string' ? msg : JSON.stringify(msg)}</p>
        </div>`;
      }

      // Automated Routine Summary Section (when triggered manually from UI)
      if (data.results && typeof data.results === 'object') {
        const jobTitle = data.job === 'sunday_pregame' ? 'Sunday Pregame Inactive Routine' : (`Weekly Routine (${data.day || 'Scheduled'})`);
        const leagueKeys = Object.keys(data.results);
        const hasErrors = leagueKeys.some(k => data.results[k] && data.results[k].error);

        html += `
          <div style="background: rgba(56, 189, 248, 0.08); border: 1px solid rgba(56, 189, 248, 0.35); border-radius: 10px; padding: 18px; margin-bottom: 20px;">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 12px;">
              <h3 style="margin: 0; color: var(--accent); font-size: 18px;">⚡ ${jobTitle} Execution Complete</h3>
              <span style="background: ${hasErrors ? '#ef4444' : '#22c55e'}; color: #000; font-weight: bold; font-size: 12px; padding: 4px 10px; border-radius: 9999px;">
                ${hasErrors ? '⚠️ PARTIAL WARNING' : '🟢 100% OPERATIONAL & SYNCED'}
              </span>
            </div>
            <p style="font-size: 13px; color: #cbd5e1; margin: 0 0 14px 0;">
              Both leagues were analyzed in parallel and digest notification was delivered to <strong>damiththa@gmail.com</strong>.
            </p>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px;">
              ${leagueKeys.map(k => {
                const ldata = data.results[k] || {};
                const lErr = ldata.error;
                const starCount = (ldata.recommended_starters || []).length;
                const strat = ldata.game_theory_strategy || (ldata.overall_waiver_strategy ? 'Waivers Evaluated' : 'Routine Completed');
                const backend = ldata.intelligence_backend || 'Gemini Pro';
                return `
                  <div style="background: #090d16; border: 1px solid ${lErr ? '#ef4444' : 'var(--border)'}; border-radius: 8px; padding: 14px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                      <strong style="color: #f8fafc; font-size: 15px;">${k}</strong>
                      <span style="color: ${lErr ? '#ef4444' : '#86efac'}; font-size: 12px; font-weight: bold;">
                        ${lErr ? '⚠️ Error' : '✅ Verified (' + backend + ')'}
                      </span>
                    </div>
                    ${lErr ? `
                      <p style="color: #fca5a5; font-size: 12px; margin: 0;">${lErr}</p>
                    ` : `
                      <div style="font-size: 12px; color: #cbd5e1; line-height: 1.4;">
                        ${strat ? `<div>🎯 Strategy: <strong style="color: #38bdf8;">${strat}</strong></div>` : ''}
                        ${starCount ? `<div>🏈 Recommended Starters: <strong>${starCount} players</strong></div>` : ''}
                      </div>
                    `}
                  </div>
                `;
              }).join('')}
            </div>
          </div>
        `;
      }

      // Verified Pipeline Confidence Banner for Individual Reports
      if (data.intelligence_backend && data.intelligence_backend !== 'deterministic_fallback') {
        html += `
          <div style="background: rgba(34, 197, 94, 0.08); border: 1px solid rgba(34, 197, 94, 0.35); border-radius: 8px; padding: 10px 14px; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">
            <div style="display: flex; align-items: center; gap: 8px;">
              <span style="font-size: 16px;">🛡️</span>
              <span style="font-size: 13px; color: #86efac; font-weight: 700;">
                Analysis Verified: 100% Live Pipeline (ESPN API • Vegas Lines • Open-Meteo • ${data.intelligence_backend})
              </span>
            </div>
            <span style="background: #22c55e; color: #0b0f19; font-size: 11px; font-weight: 800; padding: 3px 9px; border-radius: 9999px;">
              CONFIDENCE: HIGH
            </span>
          </div>
        `;
      }

      // Fallback Alert Banner
      if (data.intelligence_backend === 'deterministic_fallback') {
        html += `<div style="background: rgba(245, 158, 11, 0.15); border-left: 4px solid #f59e0b; padding: 12px 16px; margin-bottom: 16px; border-radius: 6px; font-size: 13px; color: #fbbf24; line-height: 1.4;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
            <strong>⚠️ AI Fallback Active — Rules Engine Output</strong>
            <span style="background: #f59e0b; color: #0b0f19; font-size: 11px; font-weight: 800; padding: 2px 8px; border-radius: 9999px;">CONFIDENCE: MODERATE</span>
          </div>
          Live Gemini reasoning was temporarily unavailable (${data.fallback_reason || 'connectivity check failed'}). Results were safely computed using the deterministic rules engine.
        </div>`;
      }

      // Section: Weekly Post-Game Film Room & Recap
      if (data.coach_game_summary) {
        const isWin = data.result === 'WIN';
        const isLoss = data.result === 'LOSS';
        const isInProgress = data.matchup_status === 'IN_PROGRESS';
        const isPreKickoff = data.matchup_status === 'PRE_KICKOFF';

        let outcomeColor = '#38bdf8';
        let outcomeBadge = '⚡ IN PROGRESS';
        let marginLabel = `(${data.score_margin >= 0 ? '+' : ''}${data.score_margin.toFixed(1)} pts live)`;

        if (isInProgress) {
          outcomeColor = '#f59e0b';
          outcomeBadge = `⚡ IN PROGRESS (${data.completed_starters_count || 0}/${data.total_starters_count || 9} Played)`;
          marginLabel = `(${data.score_margin >= 0 ? '+' : ''}${data.score_margin.toFixed(1)} pts live)`;
        } else if (isPreKickoff) {
          outcomeColor = '#38bdf8';
          outcomeBadge = '⏳ PRE-KICKOFF';
          marginLabel = `(${data.score_margin >= 0 ? '+' : ''}${data.score_margin.toFixed(1)} pts proj)`;
        } else if (isWin) {
          outcomeColor = '#22c55e';
          outcomeBadge = '🏆 VICTORY';
          marginLabel = `(+${data.score_margin.toFixed(1)} pts)`;
        } else if (isLoss) {
          outcomeColor = '#ef4444';
          outcomeBadge = '📉 SETBACK';
          marginLabel = `(${data.score_margin.toFixed(1)} pts)`;
        } else if (data.result === 'TIE') {
          outcomeColor = '#38bdf8';
          outcomeBadge = '⚖️ TIE';
          marginLabel = '(0.0 pts)';
        }

        const titleText = isInProgress
          ? `Week ${data.week} Film Room: Mid-Week Matchup Checkpoint`
          : (isPreKickoff ? `Week ${data.week} Film Room: Pre-Kickoff Outlook` : `Week ${data.week} Film Room & Weekly Recap`);

        const coachHeader = isInProgress
          ? `🎙️ Head Coach Mid-Week Matchup Assessment & Sunday/Monday Outlook:`
          : (isPreKickoff ? `🎙️ Head Coach Pre-Kickoff Scouting Report:` : `🎙️ Head Coach Post-Game Press Conference:`);

        const ceilingSubtitle = (isInProgress || isPreKickoff)
          ? `Starters Completed: ${data.completed_starters_count || 0} of ${data.total_starters_count || 9}`
          : `Left on Bench: ${data.points_left_on_bench.toFixed(1)} pts`;

        html += `<div style="background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 10px; padding: 18px; margin-bottom: 20px;">
          <!-- Scoreboard Header -->
          <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; border-bottom: 1px solid var(--border); padding-bottom: 14px;">
            <div>
              <div style="display: flex; align-items: center; gap: 8px;">
                <span style="font-size: 22px;">🎬</span>
                <h3 style="margin: 0; color: ${isInProgress ? '#f59e0b' : 'var(--accent)'}; font-size: 18px;">${titleText}</h3>
              </div>
              <p style="font-size: 13px; color: var(--text-muted); margin: 4px 0 0 0;">${data.league_name} • Matchup vs <strong>${data.opponent_team_name}</strong></p>
            </div>
            <span style="background: ${outcomeColor}; color: #000; font-weight: 800; padding: 6px 14px; border-radius: 9999px; font-size: 13px; letter-spacing: 0.5px;">
              ${outcomeBadge} ${marginLabel}
            </span>
          </div>

          ${isInProgress ? `
            <div style="background: rgba(245, 158, 11, 0.12); border: 1px solid rgba(245, 158, 11, 0.4); border-radius: 6px; padding: 10px 14px; margin-bottom: 14px; display: flex; align-items: center; gap: 8px;">
              <span style="font-size: 18px;">⏳</span>
              <span style="font-size: 13px; color: #fde047;"><strong>Matchup In-Progress:</strong> Showing live scores from completed games (${data.completed_starters_count || 0} of ${data.total_starters_count || 9} starters). Official Win/Loss Film Room drops Tuesday morning post-MNF!</span>
            </div>
          ` : ''}

          <!-- Dual Scoreboard Box -->
          <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 16px;">
            <div style="background: #090d16; border: 1px solid rgba(56, 189, 248, 0.4); border-radius: 8px; padding: 12px; text-align: center;">
              <span style="font-size: 12px; color: #7dd3fc; text-transform: uppercase; font-weight: 600;">${data.user_team_name} (You)</span>
              <div style="font-size: 26px; font-weight: 800; color: #38bdf8; margin: 4px 0;">${data.user_score.toFixed(1)} <span style="font-size: 13px; font-weight: normal; color: var(--text-muted);">pts</span></div>
              <span style="font-size: 11px; color: var(--text-muted);">Projected Total: ${data.user_projected.toFixed(1)} pts</span>
            </div>
            <div style="background: #090d16; border: 1px solid rgba(244, 63, 94, 0.4); border-radius: 8px; padding: 12px; text-align: center;">
              <span style="font-size: 12px; color: #fda4af; text-transform: uppercase; font-weight: 600;">${data.opponent_team_name}</span>
              <div style="font-size: 26px; font-weight: 800; color: #f43f5e; margin: 4px 0;">${data.opponent_score.toFixed(1)} <span style="font-size: 13px; font-weight: normal; color: var(--text-muted);">pts</span></div>
              <span style="font-size: 11px; color: var(--text-muted);">Projected Total: ${data.opponent_projected.toFixed(1)} pts</span>
            </div>
            <div style="background: #090d16; border: 1px solid rgba(234, 179, 8, 0.4); border-radius: 8px; padding: 12px; text-align: center;">
              <span style="font-size: 12px; color: #fde047; text-transform: uppercase; font-weight: 600;">Lineup Ceiling</span>
              <div style="font-size: 26px; font-weight: 800; color: #eab308; margin: 4px 0;">${data.optimal_lineup_points.toFixed(1)} <span style="font-size: 13px; font-weight: normal; color: var(--text-muted);">pts</span></div>
              <span style="font-size: 11px; color: #fde047;">${ceilingSubtitle}</span>
            </div>
          </div>

          <!-- Coach Press Conference Summary -->
          <div style="background: #090d16; border-left: 4px solid ${isInProgress ? '#f59e0b' : 'var(--accent)'}; border-radius: 6px; padding: 14px; margin-bottom: 16px;">
            <strong style="color: ${isInProgress ? '#f59e0b' : 'var(--accent)'}; font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px; display: block; margin-bottom: 6px;">${coachHeader}</strong>
            <p style="font-size: 14px; color: #e2e8f0; line-height: 1.5; margin: 0;">${data.coach_game_summary}</p>
          </div>

          <!-- Upcoming Starters (Yet to Play) -->
          ${data.upcoming_starters && data.upcoming_starters.length > 0 ? `
            <div style="margin-bottom: 16px;">
              <h4 style="color: #38bdf8; font-size: 14px; text-transform: uppercase; margin: 0 0 10px 0; display: flex; align-items: center; gap: 6px;">
                ⏳ Upcoming Starters (Awaiting Sunday/Monday Kickoff)
              </h4>
              <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 10px;">
                ${data.upcoming_starters.map(u => `
                  <div style="background: #090d16; border: 1px solid rgba(56, 189, 248, 0.35); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 4px;">
                      <strong style="color: #7dd3fc; font-size: 14px;">${u.player_name} (${u.position} - ${u.team})</strong>
                      <span style="color: #38bdf8; font-weight: 800; font-size: 14px;">Proj: ${u.projected_points.toFixed(1)} pts</span>
                    </div>
                    <div style="font-size: 11px; color: var(--text-muted); margin-bottom: 6px;">Slot: ${u.slot} • Status: Pending Kickoff</div>
                    <p style="font-size: 12px; color: #cbd5e1; line-height: 1.4; margin: 0;">${u.verdict_comment}</p>
                  </div>
                `).join('')}
              </div>
            </div>
          ` : ''}

          <!-- Game Balls (MVPs) -->
          ${data.game_balls && data.game_balls.length > 0 ? `
            <div style="margin-bottom: 16px;">
              <h4 style="color: #22c55e; font-size: 14px; text-transform: uppercase; margin: 0 0 10px 0; display: flex; align-items: center; gap: 6px;">
                🏆 Game Balls (${isInProgress ? 'Early Top Performers' : 'Top Performers & Smash Starts'})
              </h4>
              <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 10px;">
                ${data.game_balls.map(gb => `
                  <div style="background: #090d16; border: 1px solid rgba(34, 197, 94, 0.35); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 4px;">
                      <strong style="color: #86efac; font-size: 14px;">${gb.player_name} (${gb.position})</strong>
                      <span style="color: #22c55e; font-weight: 800; font-size: 15px;">${gb.actual_points.toFixed(1)} pts</span>
                    </div>
                    <div style="font-size: 11px; color: var(--text-muted); margin-bottom: 6px;">Proj: ${gb.projected_points.toFixed(1)} • Diff: ${gb.point_differential >= 0 ? '+' : ''}${gb.point_differential.toFixed(1)}</div>
                    <p style="font-size: 12px; color: #cbd5e1; line-height: 1.4; margin: 0;">${gb.verdict_comment}</p>
                  </div>
                `).join('')}
              </div>
            </div>
          ` : ''}

          <!-- Missed Opportunities (Bench Points Left Unplayed) -->
          ${data.missed_opportunities && data.missed_opportunities.length > 0 ? `
            <div style="margin-bottom: 16px;">
              <h4 style="color: #f59e0b; font-size: 14px; text-transform: uppercase; margin: 0 0 10px 0; display: flex; align-items: center; gap: 6px;">
                🤦 Points Left on the Bench (Suboptimal Starts)
              </h4>
              <div style="display: grid; grid-template-columns: 1fr; gap: 10px;">
                ${data.missed_opportunities.map(mo => `
                  <div style="background: #090d16; border: 1px solid rgba(245, 158, 11, 0.35); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 6px;">
                      <span style="font-size: 13px; color: #fde047;">Bench: <strong>${mo.bench_player} (${mo.bench_points.toFixed(1)} pts)</strong> outscored Starter: <strong>${mo.started_player} (${mo.starter_points.toFixed(1)} pts)</strong></span>
                      <span style="background: rgba(245, 158, 11, 0.2); color: #fde047; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: bold;">+${mo.points_differential.toFixed(1)} pts missed</span>
                    </div>
                    <p style="font-size: 12px; color: #cbd5e1; line-height: 1.4; margin: 0;">💡 <strong>Coaching Lesson:</strong> ${mo.lesson}</p>
                  </div>
                `).join('')}
              </div>
            </div>
          ` : ''}

          <!-- Busts / Underperformers -->
          ${data.busts && data.busts.length > 0 ? `
            <div style="margin-bottom: 16px;">
              <h4 style="color: #ef4444; font-size: 14px; text-transform: uppercase; margin: 0 0 10px 0; display: flex; align-items: center; gap: 6px;">
                📉 Underperformers & Tape Breakdown (${isInProgress ? 'Completed Games Only' : 'Starters'})
              </h4>
              <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 10px;">
                ${data.busts.map(b => `
                  <div style="background: #090d16; border: 1px solid rgba(239, 68, 68, 0.35); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 4px;">
                      <strong style="color: #fca5a5; font-size: 14px;">${b.player_name} (${b.position})</strong>
                      <span style="color: #ef4444; font-weight: 800; font-size: 15px;">${b.actual_points.toFixed(1)} pts</span>
                    </div>
                    <div style="font-size: 11px; color: var(--text-muted); margin-bottom: 6px;">Proj: ${b.projected_points.toFixed(1)} • Diff: ${b.point_differential >= 0 ? '+' : ''}${b.point_differential.toFixed(1)}</div>
                    <p style="font-size: 12px; color: #cbd5e1; line-height: 1.4; margin: 0;">${b.verdict_comment}</p>
                  </div>
                `).join('')}
              </div>
            </div>
          ` : ''}

          <!-- Lessons Learned & Action Priorities -->
          <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px;">
            ${data.lessons_learned && data.lessons_learned.length > 0 ? `
              <div style="background: #090d16; border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                <strong style="color: #38bdf8; font-size: 13px; text-transform: uppercase; display: block; margin-bottom: 8px;">📋 Film Room Takeaways:</strong>
                <ul style="margin: 0; padding-left: 18px; font-size: 12px; color: #cbd5e1; line-height: 1.5;">
                  ${data.lessons_learned.map(l => `<li style="margin-bottom: 4px;">${l}</li>`).join('')}
                </ul>
              </div>
            ` : ''}

            ${data.next_week_priorities && data.next_week_priorities.length > 0 ? `
              <div style="background: #090d16; border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                <strong style="color: #a855f7; font-size: 13px; text-transform: uppercase; display: block; margin-bottom: 8px;">🎯 ${isInProgress ? 'Sunday Slate & Waiver Watchlist' : `Week ${data.week + 1} Action Plan`}:</strong>
                <ul style="margin: 0; padding-left: 18px; font-size: 12px; color: #cbd5e1; line-height: 1.5;">
                  ${data.next_week_priorities.map(p => `<li style="margin-bottom: 4px;">${p}</li>`).join('')}
                </ul>
              </div>
            ` : ''}
          </div>
        </div>`;
      }

      // Section 0: Emergency Starting Hole Alert Banner
      if (data.lineup_hole_alerts && data.lineup_hole_alerts.length > 0) {
        html += `<div style="background: rgba(239, 68, 68, 0.15); border: 2px solid #ef4444; border-radius: 10px; padding: 18px; margin-bottom: 20px; box-shadow: 0 0 20px rgba(239, 68, 68, 0.25);">
          <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 12px;">
            <span style="font-size: 26px;">🚨</span>
            <div>
              <h3 style="color: #ef4444; margin: 0; font-size: 17px; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 800;">
                EMERGENCY STARTING HOLE DETECTED (${data.lineup_hole_alerts.length} SLOTS AT RISK)
              </h3>
              <p style="font-size: 13px; color: #fca5a5; margin: 2px 0 0 0;">
                Immediate action required before kickoff! You have starting slots that are currently unplayable (OUT, IR, SUS, or Bye Week) or unfilled on ESPN.
              </p>
            </div>
          </div>`;

        data.lineup_hole_alerts.forEach(alert => {
          html += `<div style="background: #090d16; border: 1px solid rgba(239, 68, 68, 0.4); border-radius: 8px; padding: 14px; margin-bottom: 12px;">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 8px;">
              <strong style="color: #f87171; font-size: 15px;">Slot: ${alert.slot}</strong>
              <span style="background: #ef4444; color: white; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: bold;">${alert.current_status}</span>
            </div>
            ${alert.current_player_name ? `<p style="font-size: 13px; color: #cbd5e1; margin-bottom: 10px;">Current Starter: <strong style="color: #f87171;">${alert.current_player_name}</strong> is unplayable.</p>` : ''}

            <!-- 3-Tier Multi-Option Recommendations -->
            <div style="display: grid; grid-template-columns: 1fr; gap: 8px; margin-top: 8px;">
              ${alert.bench_recommendation ? `
                <div style="background: rgba(34, 197, 94, 0.08); border-left: 3px solid #22c55e; padding: 8px 12px; border-radius: 4px; font-size: 13px; color: #86efac; line-height: 1.4;">
                  <strong>🔄 1. Internal Bench Fix:</strong> ${alert.bench_recommendation}
                </div>
              ` : ''}
              ${alert.waiver_recommendation ? `
                <div style="background: rgba(56, 189, 248, 0.08); border-left: 3px solid #38bdf8; padding: 8px 12px; border-radius: 4px; font-size: 13px; color: #7dd3fc; line-height: 1.4;">
                  <strong>🎯 2. Free Agency / Waiver Pickup:</strong> ${alert.waiver_recommendation}
                </div>
              ` : ''}
              ${alert.trade_recommendation ? `
                <div style="background: rgba(168, 85, 247, 0.08); border-left: 3px solid #a855f7; padding: 8px 12px; border-radius: 4px; font-size: 13px; color: #d8b4fe; line-height: 1.4;">
                  <strong>🤝 3. Trade Market Solution:</strong> ${alert.trade_recommendation}
                </div>
              ` : ''}
            </div>
          </div>`;
        });

        html += `</div>`;
      }

      // Section 1: Immediate Action Plan Banner
      if (data.vacant_slots && data.vacant_slots.length > 0) {
        html += `<div class="alert-box alert-danger">
          <h4 style="color: #ef4444; margin-bottom: 6px; font-size: 14px;">🚨 VACANT STARTING SLOTS ON ESPN</h4>
          <p style="font-size: 13px; margin-bottom: 6px;">You have empty starting slots. Fill them before kickoff:</p>
          <ul style="margin: 0; padding-left: 20px; font-size: 13px;">${data.vacant_slots.map(s => `<li>Slot <strong>${s}</strong> is currently UNFILLED</li>`).join('')}</ul>
        </div>`;
      }

      if (data.actionable_swaps && data.actionable_swaps.length > 0) {
        html += `<div class="alert-box alert-warning">
          <h4 style="color: #f59e0b; margin-bottom: 6px; font-size: 14px;">⚡ IMMEDIATE ACTION REQUIRED (${data.actionable_swaps.length} SWAPS NEEDED)</h4>
          <p style="font-size: 13px; margin-bottom: 6px;">Execute these swaps in your ESPN app:</p>
          <ul style="margin: 0; padding-left: 20px; font-size: 13px; font-weight: 600;">${data.actionable_swaps.map(s => `<li style="margin-bottom: 4px;">${s}</li>`).join('')}</ul>
        </div>`;
      } else if ((!data.vacant_slots || data.vacant_slots.length === 0) && (!data.lineup_hole_alerts || data.lineup_hole_alerts.length === 0)) {
        if (data.current_lineup && data.current_lineup.length > 0) {
          html += `<div class="alert-box alert-success">
            <h4 style="color: #22c55e; margin-bottom: 4px; font-size: 14px;">✅ LINEUP 100% OPTIMAL</h4>
            <p style="font-size: 13px; margin: 0;">All optimal starters are currently in your ESPN starting lineup! No adjustments needed.</p>
          </div>`;
        }
      }

      // Section 2: Game Theory Strategy & Projected Margin
      if (data.game_theory_strategy) {
        html += `<div style="background: rgba(56, 189, 248, 0.08); border: 1px solid rgba(56, 189, 248, 0.25); border-radius: 8px; padding: 12px 14px; margin-bottom: 16px;">
          <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 6px;">
            <span class="strategy-badge" style="background: var(--accent-dark); color: white;">Strategy: ${data.game_theory_strategy}</span>
            ${data.projected_point_differential !== undefined && data.projected_point_differential !== null ? `<span style="font-size: 13px; color: ${data.projected_point_differential >= 0 ? '#86efac' : '#fca5a5'}; font-weight: 600;">${data.projected_point_differential >= 0 ? '+' : ''}${data.projected_point_differential.toFixed(1)} Projected Margin</span>` : ''}
          </div>
          ${data.strategy_reasoning ? `<p style="font-size: 13px; color: #cbd5e1; margin: 0;"><em>"${data.strategy_reasoning}"</em></p>` : ''}
        </div>`;
      }

      // Section 2.5: Opponent Matchup Scouting Card & Counter-Strategy
      if (data.opponent_name) {
        const oppProj = data.opponent_projected_total !== undefined && data.opponent_projected_total !== null ? data.opponent_projected_total.toFixed(1) : '--';
        const oppAct = data.opponent_actual_total !== undefined && data.opponent_actual_total !== null && data.opponent_actual_total > 0 ? `${data.opponent_actual_total.toFixed(1)} pts` : null;
        const myProj = data.projected_total_points !== undefined && data.projected_total_points !== null ? data.projected_total_points.toFixed(1) : '--';
        const myAct = data.actual_total_points !== undefined && data.actual_total_points !== null && data.actual_total_points > 0 ? `${data.actual_total_points.toFixed(1)} pts` : null;

        html += `<div style="background: #090d16; border: 1px solid rgba(168, 85, 247, 0.4); border-radius: 8px; padding: 14px; margin-bottom: 16px;">
          <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 8px;">
            <div style="display: flex; align-items: center; gap: 8px;">
              <span style="font-size: 18px;">⚔️</span>
              <strong style="color: #c084fc; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;">Matchup Opponent: ${data.opponent_name}</strong>
            </div>
            <div style="font-size: 12px; color: #cbd5e1; display: flex; gap: 12px;">
              <span>You: <strong style="color: #38bdf8;">${myAct ? myAct + ' (Live)' : myProj + ' Proj'}</strong></span>
              <span style="color: #64748b;">vs</span>
              <span>Opponent: <strong style="color: #f43f5e;">${oppAct ? oppAct + ' (Live)' : oppProj + ' Proj'}</strong></span>
            </div>
          </div>
          ${data.opponent_matchup_breakdown ? `<p style="font-size: 13px; color: #cbd5e1; line-height: 1.5; margin: 0;">🛡️ <strong>Coach's Matchup Counter-Strategy:</strong> ${data.opponent_matchup_breakdown}</p>` : ''}
        </div>`;
      }

      // Helper for player matchup & schedule badge
      const getMatchupBadge = (p) => {
        if (!p.matchup_display && !p.game_time) return '';
        const isBye = p.matchup_display === 'BYE' || p.game_time === 'Bye Week';
        if (isBye) {
          return '<span class="matchup-badge bye">BYE WEEK</span>';
        }
        const ha = p.home_away ? ` (${p.home_away === 'HOME' ? 'Home' : 'Away'})` : '';
        const m = p.matchup_display ? `${p.matchup_display}${ha}` : '';
        const t = p.game_time || '';
        const label = [m, t].filter(Boolean).join(' • ');
        return `<span class="matchup-badge">🗓️ ${label}</span>`;
      };

      // Section 3: Current Starting Lineup (On ESPN As-Is)
      if (data.current_lineup && data.current_lineup.length > 0) {
        const actualTotal = data.actual_total_points !== undefined && data.actual_total_points !== null ? data.actual_total_points : null;
        const projTotal = data.projected_total_points !== undefined && data.projected_total_points !== null ? data.projected_total_points : null;
        const totalHeader = (actualTotal !== null && actualTotal > 0)
          ? `<span style="font-size: 13px; font-weight: normal; margin-left: auto; color: #cbd5e1; background: rgba(56, 189, 248, 0.15); border: 1px solid rgba(56, 189, 248, 0.3); border-radius: 6px; padding: 4px 10px;">
               Live Score: <strong style="color: #38bdf8;">${actualTotal.toFixed(1)}</strong> / Proj: <strong>${projTotal ? projTotal.toFixed(1) : '0.0'}</strong> pts
             </span>`
          : (projTotal ? `<span style="font-size: 13px; font-weight: normal; margin-left: auto; color: #94a3b8;">Proj: ${projTotal.toFixed(1)} pts</span>` : '');

        html += `<h3 style="font-size: 16px; color: #f8fafc; margin-top: 18px; margin-bottom: 10px; display: flex; align-items: center; gap: 6px;">
          🏈 Current Starting Lineup <span style="font-size: 12px; font-weight: normal; color: var(--text-muted);">(On ESPN As-Is)</span>
          ${totalHeader}
        </h3>`;
        for (const p of data.current_lineup) {
          const isBenchNow = p.action === 'BENCH_NOW';
          const isLocked = p.has_played || (p.action_label && p.action_label.includes('LOCKED'));
          const cardClass = isLocked ? 'card-keep' : (isBenchNow ? 'card-bench-now' : 'card-keep');
          const badgeClass = isLocked ? 'badge-keep' : (isBenchNow ? 'badge-bench-now' : 'badge-keep');
          const injuryBadge = p.injury_status && p.injury_status !== 'NORMAL' && p.injury_status !== 'ACTIVE'
            ? `<span style="color: #ef4444; font-weight: bold; font-size: 11px; background: rgba(239, 68, 68, 0.2); padding: 2px 6px; border-radius: 4px;">⚠️ ${p.injury_status}</span>`
            : '';

          const pointsDisplay = p.has_played
            ? `<div style="text-align: right;">
                 <div style="color: #38bdf8; font-weight: bold; font-size: 14px;">🏁 ${p.actual_points !== undefined && p.actual_points !== null ? p.actual_points.toFixed(1) : '0.0'} pts</div>
                 <div style="color: #94a3b8; font-size: 11px;">Proj: ${p.projected_points ? p.projected_points.toFixed(1) : '0.0'}</div>
               </div>`
            : `<div style="text-align: right;">
                 <span class="player-proj">${p.projected_points ? p.projected_points.toFixed(1) : '0.0'} pts</span>
                 <div style="color: #64748b; font-size: 11px;">Actual: --</div>
               </div>`;

          html += `<div class="player-card ${cardClass}">
            <div class="player-header">
              <div class="player-info">
                <span class="slot-badge">${p.current_slot || p.position}</span>
                <span class="player-name">${p.player_name}</span>
                <span class="player-meta">${p.position} • ${p.team}</span>
                ${getMatchupBadge(p)}
                ${injuryBadge}
              </div>
              <div style="display: flex; align-items: center; gap: 8px;">
                ${pointsDisplay}
                <span class="action-badge ${badgeClass}">${p.action_label || (isBenchNow ? '🚨 BENCH' : '✅ START')}</span>
              </div>
            </div>
            <div class="action-detail">${p.action_detail || ''}</div>
            <div class="player-stats">
              ${p.floor !== undefined ? `<span>Floor: <strong>${p.floor}</strong></span>` : ''}
              ${p.ceiling !== undefined ? `<span>Ceiling: <strong>${p.ceiling}</strong></span>` : ''}
              ${p.game_script_note ? `<span>• <em>${p.game_script_note}</em></span>` : ''}
            </div>
          </div>`;
        }
      } else if (data.recommended_starters && data.recommended_starters.length > 0) {
        // Fallback if current_lineup not present
        html += '<h3 style="color: #22c55e; margin-top: 16px; margin-bottom: 8px;">🟢 Optimal Starters</h3>';
        for (const p of data.recommended_starters) {
          const recPointsDisplay = p.has_played
            ? `<div style="text-align: right;">
                 <div style="color: #38bdf8; font-weight: bold; font-size: 14px;">🏁 ${p.actual_points !== undefined && p.actual_points !== null ? p.actual_points.toFixed(1) : '0.0'} pts</div>
                 <div style="color: #94a3b8; font-size: 11px;">Proj: ${p.projected_points ? p.projected_points.toFixed(1) : '0.0'}</div>
               </div>`
            : `<span class="player-proj">${p.projected_points ? p.projected_points.toFixed(1) : '0.0'} pts</span>`;

          html += `<div class="player-card card-keep">
            <div class="player-header">
              <div class="player-info">
                <span class="slot-badge">${p.position}</span>
                <span class="player-name">${p.player_name}</span>
                <span class="player-meta">${p.team}</span>
                ${getMatchupBadge(p)}
              </div>
              ${recPointsDisplay}
            </div>
            <div class="action-detail">${p.reasoning || ''}</div>
          </div>`;
        }
      }

      // Section 4: Current Bench (On ESPN As-Is)
      if (data.current_bench && data.current_bench.length > 0) {
        html += `<h3 style="font-size: 16px; color: #f8fafc; margin-top: 22px; margin-bottom: 10px; display: flex; align-items: center; gap: 6px;">
          ⏸️ Current Bench <span style="font-size: 12px; font-weight: normal; color: var(--text-muted);">(On ESPN As-Is)</span>
        </h3>`;
        for (const p of data.current_bench) {
          const isPromote = p.action === 'PROMOTE_TO_START';
          const cardClass = isPromote ? 'card-promote' : 'card-stay-bench';
          const badgeClass = isPromote ? 'badge-promote' : 'badge-stay-bench';
          const injuryBadge = p.injury_status && p.injury_status !== 'NORMAL' && p.injury_status !== 'ACTIVE'
            ? `<span style="color: #ef4444; font-weight: bold; font-size: 11px; background: rgba(239, 68, 68, 0.2); padding: 2px 6px; border-radius: 4px;">⚠️ ${p.injury_status}</span>`
            : '';

          const benchPointsDisplay = p.has_played
            ? `<div style="text-align: right;">
                 <div style="color: #38bdf8; font-weight: bold; font-size: 13px;">🏁 ${p.actual_points !== undefined && p.actual_points !== null ? p.actual_points.toFixed(1) : '0.0'} pts</div>
                 <div style="color: #94a3b8; font-size: 11px;">Proj: ${p.projected_points ? p.projected_points.toFixed(1) : '0.0'}</div>
               </div>`
            : `<div style="text-align: right;">
                 <span class="player-proj" style="color: #94a3b8;">${p.projected_points ? p.projected_points.toFixed(1) : '0.0'} pts</span>
               </div>`;

          html += `<div class="player-card ${cardClass}">
            <div class="player-header">
              <div class="player-info">
                <span class="slot-badge">${p.position}</span>
                <span class="player-name">${p.player_name}</span>
                <span class="player-meta">${p.team}</span>
                ${getMatchupBadge(p)}
                ${injuryBadge}
              </div>
              <div style="display: flex; align-items: center; gap: 8px;">
                ${benchPointsDisplay}
                <span class="action-badge ${badgeClass}">${p.action_label || (isPromote ? '⚡ START' : '⏸️ BENCH')}</span>
              </div>
            </div>
            <div class="action-detail">${p.action_detail || ''}</div>
          </div>`;
        }
      }


      // Section 5: Key Start/Sit Decisions
      if (data.key_flex_decisions && data.key_flex_decisions.length > 0) {
        html += `<div style="background: var(--card-subtle); border-left: 4px solid var(--warning); border-radius: 8px; padding: 14px; margin-top: 20px;">
          <h4 style="color: var(--warning); margin-bottom: 8px; font-size: 14px;">⚖️ KEY START/SIT DILEMMAS & FLEX CALLS</h4>
          <ul style="margin: 0; padding-left: 18px; color: #cbd5e1; font-size: 13px; line-height: 1.5;">${data.key_flex_decisions.map(d => `<li style="margin-bottom: 4px;">${d}</li>`).join('')}</ul>
        </div>`;
      }

      // Section 5.5: Waiver Wire Analysis (WaiverReport)
      if (data.overall_waiver_strategy !== undefined || data.targets !== undefined) {
        if (data.coach_verdict === 'STAND_PAT' || (!data.targets || data.targets.length === 0)) {
          html += `<div style="background: rgba(34, 197, 94, 0.1); border: 2px solid #22c55e; border-radius: 10px; padding: 18px; margin-top: 20px; margin-bottom: 20px;">
            <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px;">
              <span style="font-size: 26px;">🛡️</span>
              <div>
                <h3 style="color: #22c55e; margin: 0; font-size: 17px; text-transform: uppercase; font-weight: 800;">
                  COACH'S VERDICT: STAND PAT (NO WAIVER MOVES RECOMMENDED)
                </h3>
                <span style="background: #22c55e; color: #0f172a; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: bold;">ROSTER STATUS: HEALTHY & OPTIMAL</span>
              </div>
            </div>
            <p style="font-size: 14px; color: #e2e8f0; line-height: 1.5; margin: 10px 0 6px 0;">
              ${data.stand_pat_reasoning || data.overall_waiver_strategy || 'Your active starters are locked in and healthy, and your bench provides crucial high-upside depth. None of the available waiver options represent a meaningful upgrade over your current assets.'}
            </p>
            <div style="background: #090d16; border: 1px solid rgba(34, 197, 94, 0.3); padding: 10px 12px; border-radius: 6px; font-size: 12px; color: #86efac; margin-top: 10px;">
              💡 <strong>Coach's Golden Rule:</strong> Churning the bottom of your roster for marginal sidegrades burns rolling waiver priority and forfeits valuable backup stashes. Hold your bench depth!
            </div>
          </div>`;
        } else {
          html += `<h3 style="color: #eab308; margin-top: 20px; margin-bottom: 12px; font-size: 16px;">🔄 RECOMMENDED WAIVER WIRE TARGETS</h3>`;
          if (data.overall_waiver_strategy) {
            html += `<div style="background: var(--card-subtle); padding: 12px 14px; border-radius: 8px; margin-bottom: 12px; border: 1px solid var(--border);"><p style="font-size: 13px; margin: 0; color: #cbd5e1;">📋 <strong>Waiver Strategy:</strong> ${data.overall_waiver_strategy}</p></div>`;
          }
          data.targets.forEach(t => {
            const prioColor = t.priority === 'MUST_ADD' ? '#ef4444' : (t.priority === 'HIGH' ? '#f59e0b' : '#38bdf8');
            html += `<div style="background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 14px; margin-bottom: 12px;">
              <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; flex-wrap: wrap; gap: 6px;">
                <strong style="font-size: 15px; color: #f8fafc;">⬆️ ADD ${t.player_name} <span style="font-size: 12px; color: var(--text-muted);">(${t.position} - ${t.team})</span></strong>
                <span style="background: ${prioColor}; color: #0f172a; font-weight: bold; font-size: 11px; padding: 2px 8px; border-radius: 4px;">${t.priority}</span>
              </div>
              ${t.recommended_drop ? `<p style="font-size: 13px; color: #f87171; margin-bottom: 6px;"><strong>Suggested Drop:</strong> ${t.recommended_drop}</p>` : ''}
              <p style="font-size: 13px; color: #cbd5e1; margin-bottom: 4px;"><strong>Why Claim:</strong> ${t.reasoning}</p>
              ${t.upside_summary ? `<p style="font-size: 12px; color: #94a3b8; margin: 0;"><strong>Upside:</strong> ${t.upside_summary}</p>` : ''}
            </div>`;
          });
        }
      }

      // Section 6: Proactive Trade Proposals with 1-tap Copy Pitch
      if (data.proposals && data.proposals.length > 0) {
        if (data.market_overview) {
          html += `<div style="background: var(--card-subtle); padding: 12px 14px; border-radius: 8px; margin-top: 20px; margin-bottom: 12px; border: 1px solid var(--border);"><p style="font-size: 13px; margin: 0; color: #cbd5e1;">📊 <strong>Market Analysis:</strong> <em>"${data.market_overview}"</em></p></div>`;
        }
        html += '<h3 style="color: var(--accent); margin-top: 20px; margin-bottom: 12px; font-size: 16px;">💡 PROACTIVE WIN-WIN TRADE PROPOSALS</h3>';
        data.proposals.forEach((tp, idx) => {
          const giving = (tp.giving_players || []).join(', ');
          const recving = (tp.receiving_players || []).join(', ');
          const mgrStr = tp.target_manager ? ` (${tp.target_manager})` : '';
          const btnId = `btn-pitch-${idx}`;
          const pitchSafe = (tp.negotiation_pitch || '').replace(/'/g, "\\\\'").replace(/"/g, '&quot;');
          const isWeekly = tp.time_horizon === 'WEEKLY_PURPOSE';
          const horizonBadge = isWeekly
            ? '<span style="background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid #d97706; font-weight: bold; font-size: 11px; padding: 2px 8px; border-radius: 9999px;">⚡ WEEKLY / MATCHUP PURPOSE</span>'
            : '<span style="background: rgba(56, 189, 248, 0.2); color: #38bdf8; border: 1px solid #0284c7; font-weight: bold; font-size: 11px; padding: 2px 8px; border-radius: 9999px;">🎯 LONG-TERM / ROS DECISION</span>';

          html += `<div class="trade-card">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; flex-wrap: wrap; gap: 6px;">
              <strong style="font-size: 15px; color: #f8fafc;">🤝 Trade with ${tp.target_team_name}${mgrStr}</strong>
              <div style="display: flex; gap: 6px; align-items: center; flex-wrap: wrap;">
                ${horizonBadge}
                <span style="color: var(--success); font-weight: bold; font-size: 12px; background: rgba(34, 197, 94, 0.15); padding: 2px 8px; border-radius: 4px;">+${tp.net_vorp_gain > 0 ? tp.net_vorp_gain : 0} Weekly VORP</span>
              </div>
            </div>
            <p style="margin-bottom: 8px; font-size: 13px;">
              <span style="color: #ef4444; font-weight: bold;">Give:</span> <span style="color: #f8fafc;">${giving}</span>
              <span style="color: #94a3b8; margin: 0 6px;">➔</span>
              <span style="color: var(--success); font-weight: bold;">Receive:</span> <span style="color: #f8fafc;">${recving}</span>
            </p>
            ${tp.time_horizon_detail ? `<p style="font-size: 12px; color: #94a3b8; margin-bottom: 6px;"><strong>Strategic Horizon:</strong> ${tp.time_horizon_detail}</p>` : ''}
            <p style="font-size: 13px; color: #cbd5e1; margin-bottom: 4px;"><strong style="color: var(--accent);">Your Upgrade:</strong> ${tp.your_lineup_upgrade}</p>
            <p style="font-size: 13px; color: #cbd5e1; margin-bottom: 10px;"><strong style="color: #a78bfa;">Why Target Accepts:</strong> ${tp.why_target_accepts}</p>
            ${tp.coach_conviction ? `
              <div style="background: rgba(168, 85, 247, 0.1); border-left: 4px solid #a855f7; padding: 10px 14px; border-radius: 6px; margin-bottom: 10px; font-size: 13px; line-height: 1.45;">
                <strong style="color: #c084fc; display: flex; align-items: center; gap: 6px; margin-bottom: 4px;">
                  <span>🗣️</span> COACH'S CONVICTION (WHY YOU CONSIDER THIS):
                </strong>
                <span style="color: #f1f5f9; font-style: italic;">"${tp.coach_conviction}"</span>
              </div>
            ` : ''}
            <div style="background: #090d16; border: 1px solid var(--border); padding: 10px 12px; border-radius: 6px; font-size: 13px; color: #38bdf8; line-height: 1.4;">
              💬 <strong>Negotiation Pitch:</strong> "${tp.negotiation_pitch}"
            </div>
            <div class="trade-actions">
              <button id="${btnId}" class="btn-copy" onclick="copyPitch('${pitchSafe}', '${btnId}')">📋 Copy Pitch</button>
            </div>
          </div>`;
        });
      } else if (data.coach_verdict === 'HOLD_ROSTER' || (data.market_overview && (!data.proposals || data.proposals.length === 0))) {
        html += `<div style="background: rgba(56, 189, 248, 0.1); border: 2px solid #38bdf8; border-radius: 10px; padding: 18px; margin-top: 20px; margin-bottom: 20px;">
          <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px;">
            <span style="font-size: 26px;">🛡️</span>
            <div>
              <h3 style="color: #38bdf8; margin: 0; font-size: 17px; text-transform: uppercase; font-weight: 800;">
                COACH'S VERDICT: HOLD ROSTER (NO TRADES RECOMMENDED)
              </h3>
              <span style="background: #38bdf8; color: #0f172a; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: bold;">STAND PAT ON TRADE MARKET</span>
            </div>
          </div>
          <p style="font-size: 14px; color: #e2e8f0; line-height: 1.5; margin: 10px 0 6px 0;">
            ${data.hold_roster_reasoning || data.market_overview || 'Your starting lineup is strong and your bench provides crucial positional depth. No opposing teams currently offer a trade package that improves your starting lineup without compromising essential depth. Hold your assets.'}
          </p>
          <div style="background: #090d16; border: 1px solid rgba(56, 189, 248, 0.3); padding: 10px 12px; border-radius: 6px; font-size: 12px; color: #7dd3fc; margin-top: 10px;">
            💡 <strong>Coach's Golden Rule:</strong> Never force a trade for the sake of deal-making. Only trade when the incoming asset legitimately upgrades your starting lineup points without creating a dangerous positional hole.
          </div>
        </div>`;
      }

      // Section 7: Trade Evaluator Verdict
      if (data.verdict) {
        if (data.verdict === 'INVALID' || data.is_valid_trade === false) {
          html += `<div style="padding: 16px; background: rgba(239, 68, 68, 0.12); border-left: 4px solid #ef4444; border-radius: 8px; margin-bottom: 14px;">
            <h3 style="color: #ef4444; margin-bottom: 8px; font-size: 17px;">🚨 VERDICT: INVALID TRADE PROPOSAL</h3>
            <p style="font-size: 14px; color: #fca5a5; line-height: 1.5; margin-bottom: 10px;">${data.reasoning ? data.reasoning.replace(/\\n/g, '<br>') : 'Trade failed roster ownership verification.'}</p>
            ${data.roster_validation_errors && data.roster_validation_errors.length > 0 ? `
              <div style="background: rgba(0,0,0,0.3); border: 1px solid rgba(239, 68, 68, 0.35); padding: 10px 12px; border-radius: 6px; margin-bottom: 8px;">
                <strong style="color: #f87171; font-size: 13px;">Roster Ownership Violations:</strong>
                <ul style="margin: 6px 0 0 16px; color: #cbd5e1; font-size: 13px; line-height: 1.4;">
                  ${data.roster_validation_errors.map(err => `<li>${err}</li>`).join('')}
                </ul>
              </div>
            ` : ''}
            <p style="font-size: 12px; color: var(--text-muted); margin: 0;">💡 <em>Tip: You can only trade away players currently rostered on your team, and you cannot trade for players you already own.</em></p>
          </div>`;
        } else {
          const color = data.verdict === 'ACCEPT' ? '#22c55e' : (data.verdict === 'REJECT' ? '#ef4444' : '#eab308');
          const delta = data.net_starting_points_change !== null && data.net_starting_points_change !== undefined ? data.net_starting_points_change : null;
          const deltaSign = delta !== null && delta > 0 ? '+' : '';
          const isWeekly = data.time_horizon === 'WEEKLY_PURPOSE';
          const horizonBadge = isWeekly
            ? '<span style="background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid #d97706; font-weight: bold; font-size: 11px; padding: 3px 9px; border-radius: 9999px;">⚡ WEEKLY / MATCHUP PURPOSE</span>'
            : '<span style="background: rgba(56, 189, 248, 0.2); color: #38bdf8; border: 1px solid #0284c7; font-weight: bold; font-size: 11px; padding: 3px 9px; border-radius: 9999px;">🎯 LONG-TERM / ROS DECISION</span>';

          html += `<div style="padding: 16px; background: rgba(255,255,255,0.05); border-left: 4px solid ${color}; border-radius: 8px; margin-bottom: 14px;">
            <div style="display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 8px; margin-bottom: 8px;">
              <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap;">
                <h3 style="color: ${color}; margin: 0; font-size: 18px;">VERDICT: ${data.verdict}</h3>
                ${horizonBadge}
              </div>
              ${delta !== null ? `<span style="font-size: 14px; font-weight: bold; color: ${delta > 0 ? '#22c55e' : (delta < 0 ? '#ef4444' : '#eab308')};">Net Starting Lineup: ${deltaSign}${delta} pts/wk</span>` : ''}
            </div>

            <!-- Pre vs Post Starting Points Summary Box -->
            ${data.pre_trade_starting_points !== null && data.pre_trade_starting_points !== undefined ? `
              <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 8px; background: rgba(0,0,0,0.3); border: 1px solid var(--border); padding: 10px; border-radius: 6px; margin: 10px 0;">
                <div><span style="font-size: 11px; color: var(--text-muted); display: block;">Pre-Trade Starters</span><strong style="color: #cbd5e1; font-size: 15px;">${data.pre_trade_starting_points} pts</strong></div>
                <div><span style="font-size: 11px; color: var(--text-muted); display: block;">Post-Trade Starters</span><strong style="color: #cbd5e1; font-size: 15px;">${data.post_trade_starting_points} pts</strong></div>
                <div><span style="font-size: 11px; color: var(--text-muted); display: block;">Weekly Net Delta</span><strong style="color: ${delta > 0 ? '#22c55e' : (delta < 0 ? '#ef4444' : '#eab308')}; font-size: 15px;">${deltaSign}${delta} pts</strong></div>
                <div><span style="font-size: 11px; color: var(--text-muted); display: block;">Net VORP</span><strong style="color: #38bdf8; font-size: 15px;">${data.your_vorp_change > 0 ? '+' : ''}${data.your_vorp_change}</strong></div>
              </div>
            ` : ''}

            ${data.coach_conviction ? `
              <div style="background: ${data.verdict === 'ACCEPT' ? 'rgba(34, 197, 94, 0.12)' : (data.verdict === 'REJECT' ? 'rgba(239, 68, 68, 0.12)' : 'rgba(234, 179, 8, 0.12)')}; border-left: 4px solid ${color}; padding: 12px 14px; border-radius: 6px; margin: 12px 0; font-size: 13px; line-height: 1.5;">
                <strong style="color: ${color}; display: flex; align-items: center; gap: 6px; margin-bottom: 4px;">
                  <span>🗣️</span> COACH'S CONVICTION (WHY YOU CONSIDER THIS):
                </strong>
                <p style="color: #f8fafc; font-style: italic; margin: 0;">"${data.coach_conviction}"</p>
              </div>
            ` : ''}

            <p style="font-size: 14px; color: #cbd5e1; line-height: 1.5; margin: 8px 0;">${data.reasoning}</p>
            ${data.time_horizon_detail ? `<p style="font-size: 13px; color: #94a3b8; margin-top: 6px;"><strong style="color: var(--accent);">Time Horizon Scope:</strong> ${data.time_horizon_detail}</p>` : ''}

            ${data.starting_lineup_changes && data.starting_lineup_changes.length > 0 ? `
              <div style="margin: 10px 0; padding: 10px; background: #090d16; border-radius: 6px; border: 1px solid var(--border);">
                <strong style="font-size: 12px; color: var(--accent); text-transform: uppercase;">Lineup & Depth Movements:</strong>
                <ul style="margin: 6px 0 0 16px; color: #cbd5e1; font-size: 13px; line-height: 1.4;">
                  ${data.starting_lineup_changes.map(c => `<li>${c}</li>`).join('')}
                </ul>
              </div>
            ` : ''}

            ${data.positional_depth_impact ? `<p style="font-size: 13px; color: #94a3b8; margin-top: 6px;"><strong>Positional Depth:</strong> ${data.positional_depth_impact}</p>` : ''}
            ${data.counter_suggestion ? `<p style="font-size: 13px; color: #eab308; margin-top: 6px;"><strong>Strategic Counter:</strong> ${data.counter_suggestion}</p>` : ''}
            ${data.playoff_schedule_impact ? `<p style="font-size: 12px; color: #64748b; margin-top: 6px;"><strong>Playoff Weeks 15-17:</strong> ${data.playoff_schedule_impact}</p>` : ''}
          </div>`;
        }
      }

      html += '<details style="margin-top: 18px;"><summary style="cursor: pointer; color: var(--text-muted); font-size: 12px;">🔍 View Raw Technical Data (JSON)</summary><pre>' + JSON.stringify(data, null, 2) + '</pre></details>';

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


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    """Serve SVG football favicon for browser tab icon and bookmark requests."""
    svg_content = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '<circle cx="50" cy="50" r="48" fill="#0b0f19" stroke="#d9381e" stroke-width="4"/>'
        '<text x="50%" y="54%" font-size="52" text-anchor="middle" dominant-baseline="central">🏈</text>'
        '</svg>'
    )
    return Response(content=svg_content, media_type="image/svg+xml")


@app.get("/health/models")
def health_models(force: bool = False) -> dict[str, Any]:
    """Inspect real-time status of all candidate Gemini models and auto-upgrade readiness."""
    active_model, diag = negotiate_active_model(force=force)
    return {
        "active_model": active_model,
        "target_model": PREFERRED_MODELS[0],
        "auto_upgrade_enabled": True,
        "auto_upgrade_active": diag.get("auto_upgrade_active", False),
        "candidates": diag.get("candidates", {}),
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/health")
def health_check(check_gemini: bool = False) -> dict[str, Any]:
    """Health check endpoint confirming service status and configuration."""
    gemini_info = check_gemini_connectivity(force=check_gemini)
    active_model, diag = negotiate_active_model(force=False)
    return {
        "status": "healthy",
        "season": get_current_season(),
        "model": active_model,
        "target_model": PREFERRED_MODELS[0],
        "auto_upgrade_enabled": True,
        "auto_upgrade_active": diag.get("auto_upgrade_active", False),
        "gemini_status": gemini_info["status"],
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


@app.get("/run/weekly")
@app.post("/run/weekly")
def run_weekly_analysis() -> dict[str, Any]:
    """Automated weekday workflow triggered by Cloud Scheduler (Tue, Thu, Fri, Sat).
    Routes internally based on current weekday (America/New_York).
    Processes leagues concurrently to stay well below execution deadlines.
    """
    import zoneinfo

    eastern = zoneinfo.ZoneInfo("America/New_York")
    now_et = datetime.now(eastern)
    weekday = now_et.weekday()  # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
    day_name = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][
        weekday
    ]

    logger.info(f"Running automated weekly job for {day_name}...")

    results: dict[str, Any] = {}
    client = None
    try:
        client = GeminiIntelligenceClient()
    except Exception as e:
        logger.warning(
            f"Could not initialize GeminiIntelligenceClient: {e}. Falling back to deterministic mode."
        )

    def _process_single_league(league_config: Any) -> dict[str, Any]:
        league_res: dict[str, Any] = {}
        try:
            espn = LeagueClient().get_league(league_config)
            current_week = get_current_week(espn)
            my_team = next((t for t in espn.teams if t.team_id == league_config.team_id), None)
            if not my_team:
                return {league_config.short_name: {"error": f"Team {league_config.team_id} not found in league"}}

            parsed_roster = parse_roster(my_team, league_config, week=current_week)
            matchup = get_weekly_matchup(espn, league_config.team_id, current_week, league_config)

            if weekday == 1:  # Tuesday: Weekly Recap (Film Room) + Waiver Wire Analysis
                recap_week = current_week
                recap_matchup = matchup
                if matchup and current_week > 1:
                    your_lineup = matchup.your_lineup or []
                    played_count = sum(
                        1 for p in your_lineup if getattr(p, "actual_points", 0) > 0
                    )
                    if played_count == 0:
                        recap_week = current_week - 1
                        recap_matchup = get_weekly_matchup(
                            espn, league_config.team_id, recap_week, league_config
                        )
                        logger.info(
                            "ESPN week rolled to %d — recapping completed Week %d instead",
                            current_week,
                            recap_week,
                        )

                if recap_matchup:
                    recap = generate_weekly_recap(
                        league=league_config,
                        week=recap_week,
                        matchup=recap_matchup,
                        client=client,
                    )
                    league_res[f"{league_config.short_name} Film Room"] = recap.model_dump()

                # 2. Waiver Wire Analysis
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
                league_res[league_config.short_name] = report.model_dump()

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
                    espn_league=espn,
                )
                league_res[league_config.short_name] = lineup.model_dump()

            elif weekday == 5:  # Saturday: Full Matchup Preview & Scouting
                odds = fetch_week_odds()
                if matchup:
                    report = generate_matchup_preview(
                        league_config, current_week, matchup, odds=odds, client=client
                    )
                    league_res[league_config.short_name] = report.model_dump()

            else:
                league_res[league_config.short_name] = {
                    "message": f"No specific routine scheduled for {day_name}"
                }

        except Exception as e:
            logger.error(f"Error processing league {league_config.league_id}: {e}", exc_info=True)
            league_res[league_config.short_name] = {"error": str(e)}

        return league_res

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(ALL_LEAGUES)) as executor:
            future_to_league = {
                executor.submit(_process_single_league, cfg): cfg for cfg in ALL_LEAGUES.values()
            }
            for future in concurrent.futures.as_completed(future_to_league):
                results.update(future.result())

        # Send digest email
        send_digest_email("weekly_analysis", results, day=day_name)

        return {
            "job": "weekly_analysis",
            "day": day_name,
            "results": results,
            "generated_at": datetime.now(eastern).strftime("%A, %B %-d, %Y at %-I:%M %p %Z"),
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as fatal_err:
        logger.error("Fatal error in weekly_analysis: %s", fatal_err, exc_info=True)
        send_error_alert_email("weekly_analysis", str(fatal_err))
        raise HTTPException(status_code=500, detail=str(fatal_err))


@app.get("/run/sunday-pregame")
@app.post("/run/sunday-pregame")
def run_sunday_pregame() -> dict[str, Any]:
    """Sunday 90-minute pregame alert: checks active/inactive statuses, weather, and finalizes starters.
    Processes leagues concurrently to stay well below execution deadlines.
    """
    import zoneinfo

    eastern = zoneinfo.ZoneInfo("America/New_York")
    logger.info("Running Sunday pregame inactive & final lineup optimization...")

    results: dict[str, Any] = {}
    client = None
    try:
        client = GeminiIntelligenceClient()
    except Exception as e:
        logger.warning(
            f"Could not initialize GeminiIntelligenceClient: {e}. Falling back to deterministic mode."
        )

    odds = fetch_week_odds()

    def _process_single_sunday_league(league_config: Any) -> dict[str, Any]:
        league_res: dict[str, Any] = {}
        try:
            espn = LeagueClient().get_league(league_config)
            current_week = get_current_week(espn)
            my_team = next((t for t in espn.teams if t.team_id == league_config.team_id), None)
            if not my_team:
                return {league_config.short_name: {"error": f"Team {league_config.team_id} not found in league"}}

            parsed_roster = parse_roster(my_team, league_config, week=current_week)
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
                espn_league=espn,
            )
            league_res[league_config.short_name] = lineup.model_dump()

        except Exception as e:
            logger.error(f"Error in Sunday pregame for league {league_config.league_id}: {e}", exc_info=True)
            league_res[league_config.short_name] = {"error": str(e)}

        return league_res

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(ALL_LEAGUES)) as executor:
            future_to_league = {
                executor.submit(_process_single_sunday_league, cfg): cfg for cfg in ALL_LEAGUES.values()
            }
            for future in concurrent.futures.as_completed(future_to_league):
                results.update(future.result())

        # Send game-day digest email
        send_digest_email("sunday_pregame", results)

        return {
            "job": "sunday_pregame",
            "results": results,
            "generated_at": datetime.now(eastern).strftime("%A, %B %-d, %Y at %-I:%M %p %Z"),
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as fatal_err:
        logger.error("Fatal error in sunday_pregame: %s", fatal_err, exc_info=True)
        send_error_alert_email("sunday_pregame", str(fatal_err))
        raise HTTPException(status_code=500, detail=str(fatal_err))


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

        parsed_roster = parse_roster(my_team, league_config, week=current_week)
        matchup = get_weekly_matchup(espn, league_config.team_id, current_week, league_config)
        roster_names = [p.name for p in parsed_roster.players]
        injuries = get_injury_report(roster_names)
        odds = fetch_week_odds()

        client = None
        try:
            client = GeminiIntelligenceClient()
        except Exception as e:
            logger.warning("Could not initialize Gemini client for lineup: %s. Using deterministic fallback.", e)

        lineup = optimize_lineup(
            league=league_config,
            week=current_week,
            roster=parsed_roster,
            matchup=matchup,
            injuries=injuries,
            odds=odds,
            client=client,
            espn_league=espn,
        )
        return lineup.model_dump()
    except Exception as e:
        logger.error(f"Error querying lineup: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/query/roster-players")
def query_roster_players(league_id: int = Query(..., description="ESPN League ID")) -> dict[str, Any]:
    """Fetch current roster players for quick-select in trade evaluator."""
    league_config = ALL_LEAGUES.get(league_id)
    if not league_config:
        raise HTTPException(status_code=404, detail=f"League {league_id} not found in configuration.")

    try:
        espn = LeagueClient().get_league(league_config)
        current_week = get_current_week(espn)
        my_team = next((t for t in espn.teams if t.team_id == league_config.team_id), None)
        parsed_roster = parse_roster(my_team, league_config, week=current_week) if my_team else None
        players = []
        if parsed_roster:
            for p in parsed_roster.players:
                players.append({
                    "name": p.name,
                    "pos": p.position,
                    "team": p.team,
                    "pts": p.actual_points if p.has_played else p.projected_points,
                    "projected_points": p.projected_points,
                    "actual_points": p.actual_points,
                    "has_played": p.has_played,
                    "slot": p.slot,
                })
        return {
            "league_id": league_id,
            "team_name": getattr(parsed_roster, "team_name", "My Team"),
            "players": players,
        }
    except Exception as e:
        logger.error(f"Error fetching roster players: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/query/trade")
def query_trade(req: TradeRequest) -> dict[str, Any]:
    """On-demand trade evaluation evaluated against your current roster."""
    league_config = ALL_LEAGUES.get(req.league_id)
    if not league_config:
        raise HTTPException(status_code=404, detail=f"League {req.league_id} not found.")

    try:
        espn = LeagueClient().get_league(league_config)
        current_week = get_current_week(espn)
        my_team = next((t for t in espn.teams if t.team_id == league_config.team_id), None)
        parsed_roster = parse_roster(my_team, league_config, week=current_week) if my_team else None


        client = None
        try:
            client = GeminiIntelligenceClient()
        except Exception as e:
            logger.warning("Could not initialize Gemini client for trade evaluation: %s. Using deterministic fallback.", e)

        verdict = evaluate_trade(
            league=league_config,
            roster=parsed_roster,
            giving_players=req.giving_players,
            receiving_players=req.receiving_players,
            client=client,
            espn_league=espn,
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
        except Exception as e:
            logger.warning("Could not initialize Gemini client for trade proposals: %s. Using deterministic fallback.", e)

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


@app.post("/query/waivers")
def query_waivers(league_id: int = Query(..., description="ESPN League ID")) -> dict[str, Any]:
    """On-demand waiver wire analysis with veteran head-coach discipline."""
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
                status_code=404,
                detail=f"Team {league_config.team_id} not found in league {league_id}",
            )

        parsed_roster = parse_roster(my_team, league_config, week=current_week)
        free_agents = [
            {
                "name": p.name,
                "position": p.position,
                "team": p.proTeam,
                "projected_points": getattr(p, "projected_points", 0.0),
                "percent_owned": getattr(p, "percent_owned", 0.0),
            }
            for p in espn.free_agents(size=30)
        ]
        trending = fetch_trending_adds(lookback_hours=24, limit=20)
        roster_names = [p.name for p in parsed_roster.players]
        injuries = get_injury_report(roster_names)

        client = None
        try:
            client = GeminiIntelligenceClient()
        except Exception as e:
            logger.warning("Could not initialize Gemini client for waivers: %s. Using deterministic fallback.", e)

        report = evaluate_waivers(
            league=league_config,
            week=current_week,
            roster=parsed_roster,
            free_agents=free_agents,
            trending_adds=trending,
            injuries=injuries,
            client=client,
        )
        return report.model_dump()
    except Exception as e:
        logger.error(f"Error querying waivers for league {league_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/query/weekly-recap")
@app.get("/query/weekly-recap")
def query_weekly_recap(
    league_id: int = Query(..., description="ESPN League ID"),
    week: Optional[int] = Query(None, description="NFL week (defaults to current week)"),
) -> dict[str, Any]:
    """On-demand Weekly Post-Game Film Room and Recap for a specific league."""
    league_config = ALL_LEAGUES.get(league_id)
    if not league_config:
        raise HTTPException(
            status_code=404, detail=f"League {league_id} not found in configuration."
        )

    try:
        espn = LeagueClient().get_league(league_config)
        current_week = get_current_week(espn)
        target_week = week if week is not None else current_week

        matchup = get_weekly_matchup(espn, league_config.team_id, target_week, league_config)
        if not matchup:
            raise HTTPException(
                status_code=404, detail=f"Matchup for week {target_week} not found."
            )

        client = None
        try:
            client = GeminiIntelligenceClient()
        except Exception as e:
            logger.warning("Could not initialize Gemini client for recap: %s. Using deterministic fallback.", e)

        report = generate_weekly_recap(
            league=league_config,
            week=target_week,
            matchup=matchup,
            client=client,
        )
        return report.model_dump()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error querying weekly recap for league {league_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))



