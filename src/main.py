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
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, viewport-fit=cover">
  <title>Mad Dawg's Fantasy Football Command Center</title>
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
      <p>AI Fantasy Intelligence • Zero-Cost Cloud Run • Gemini 2.5 Pro</p>
      <div class="status-bar">
        <span class="status-badge">⚡ Status: Operational</span>
        <span class="status-badge">🧠 Brain: Gemini 2.5 Pro</span>
        <span class="status-badge">🏈 Season: 2026</span>
        <span class="status-badge">🌿 Power: us-central1</span>
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
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px;">
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
      <p>API Access: <a href="/docs" target="_blank">Swagger Documentation (/docs)</a> • <a href="/health" target="_blank">Health Status (/health)</a></p>
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
      } else if (!data.vacant_slots || data.vacant_slots.length === 0) {
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
        html += `<h3 style="font-size: 16px; color: #f8fafc; margin-top: 18px; margin-bottom: 10px; display: flex; align-items: center; gap: 6px;">
          🏈 Current Starting Lineup <span style="font-size: 12px; font-weight: normal; color: var(--text-muted);">(On ESPN As-Is)</span>
        </h3>`;
        for (const p of data.current_lineup) {
          const isBenchNow = p.action === 'BENCH_NOW';
          const cardClass = isBenchNow ? 'card-bench-now' : 'card-keep';
          const badgeClass = isBenchNow ? 'badge-bench-now' : 'badge-keep';
          const injuryBadge = p.injury_status && p.injury_status !== 'NORMAL' && p.injury_status !== 'ACTIVE'
            ? `<span style="color: #ef4444; font-weight: bold; font-size: 11px; background: rgba(239, 68, 68, 0.2); padding: 2px 6px; border-radius: 4px;">⚠️ ${p.injury_status}</span>`
            : '';

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
                <span class="player-proj">${p.projected_points ? p.projected_points.toFixed(1) : '0.0'} pts</span>
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
          html += `<div class="player-card card-keep">
            <div class="player-header">
              <div class="player-info">
                <span class="slot-badge">${p.position}</span>
                <span class="player-name">${p.player_name}</span>
                <span class="player-meta">${p.team}</span>
                ${getMatchupBadge(p)}
              </div>
              <span class="player-proj">${p.projected_points ? p.projected_points.toFixed(1) : '0.0'} pts</span>
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
                <span class="player-proj" style="color: #94a3b8;">${p.projected_points ? p.projected_points.toFixed(1) : '0.0'} pts</span>
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
          html += `<div class="trade-card">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; flex-wrap: wrap; gap: 6px;">
              <strong style="font-size: 15px; color: #f8fafc;">🤝 Trade with ${tp.target_team_name}${mgrStr}</strong>
              <span style="color: var(--success); font-weight: bold; font-size: 12px; background: rgba(34, 197, 94, 0.15); padding: 2px 8px; border-radius: 4px;">+${tp.net_vorp_gain > 0 ? tp.net_vorp_gain : 0} Weekly VORP</span>
            </div>
            <p style="margin-bottom: 8px; font-size: 13px;">
              <span style="color: #ef4444; font-weight: bold;">Give:</span> <span style="color: #f8fafc;">${giving}</span>
              <span style="color: #94a3b8; margin: 0 6px;">➔</span>
              <span style="color: var(--success); font-weight: bold;">Receive:</span> <span style="color: #f8fafc;">${recving}</span>
            </p>
            <p style="font-size: 13px; color: #cbd5e1; margin-bottom: 4px;"><strong style="color: var(--accent);">Your Upgrade:</strong> ${tp.your_lineup_upgrade}</p>
            <p style="font-size: 13px; color: #cbd5e1; margin-bottom: 10px;"><strong style="color: #a78bfa;">Why Target Accepts:</strong> ${tp.why_target_accepts}</p>
            <div style="background: #090d16; border: 1px solid var(--border); padding: 10px 12px; border-radius: 6px; font-size: 13px; color: #38bdf8; line-height: 1.4;">
              💬 <strong>Negotiation Pitch:</strong> "${tp.negotiation_pitch}"
            </div>
            <div class="trade-actions">
              <button id="${btnId}" class="btn-copy" onclick="copyPitch('${pitchSafe}', '${btnId}')">📋 Copy Pitch</button>
            </div>
          </div>`;
        });
      }

      // Section 7: Trade Evaluator Verdict
      if (data.verdict) {
        const color = data.verdict === 'ACCEPT' ? '#22c55e' : (data.verdict === 'REJECT' ? '#ef4444' : '#eab308');
        html += `<div style="padding: 14px; background: rgba(255,255,255,0.05); border-left: 4px solid ${color}; border-radius: 8px; margin-bottom: 14px;">
          <h3 style="color: ${color}; margin-bottom: 6px; font-size: 16px;">VERDICT: ${data.verdict} (Net VORP: ${data.your_vorp_change > 0 ? '+' : ''}${data.your_vorp_change})</h3>
          <p style="font-size: 14px; color: #cbd5e1; line-height: 1.4;">${data.reasoning}</p>
          <p style="font-size: 13px; color: #94a3b8; margin-top: 6px;"><strong>Starting Lineup Impact:</strong> ${data.starting_lineup_impact}</p>
          ${data.counter_suggestion ? '<p style="font-size: 13px; color: #eab308; margin-top: 4px;"><strong>Counter Idea:</strong> ' + data.counter_suggestion + '</p>' : ''}
        </div>`;
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
        "generated_at": datetime.now(eastern).strftime("%A, %B %-d, %Y at %-I:%M %p %Z"),
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

    import zoneinfo
    eastern = zoneinfo.ZoneInfo("America/New_York")

    return {
        "job": "sunday_pregame",
        "results": results,
        "generated_at": datetime.now(eastern).strftime("%A, %B %-d, %Y at %-I:%M %p %Z"),
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

