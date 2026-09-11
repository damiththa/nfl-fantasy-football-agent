"""
SendGrid email integration for fantasy football digest notifications.

Uses SendGrid's v3 HTTP API via httpx (already a project dependency).
Sends beautiful HTML digest emails after each automated analysis run.
Cost: $0 — SendGrid free tier covers 100 emails/day, we send ~4/week.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger("fantasy_agent.email")

SENDGRID_API_URL = "https://api.sendgrid.com/v3/mail/send"

# Recipient — Mad Dawg's email
RECIPIENT_EMAIL = "damiththa@gmail.com"
RECIPIENT_NAME = "Mad Dawg"

# Sender — must be a verified sender in SendGrid
SENDER_EMAIL = "damiththa@gmail.com"
SENDER_NAME = "Fantasy Football Agent 🏈"


def _get_sendgrid_key() -> str | None:
    """Fetch SendGrid API key from environment (injected via Secret Manager on Cloud Run)."""
    key = os.environ.get("SENDGRID_API_KEY")
    if not key:
        logger.warning("SENDGRID_API_KEY not set — email notifications disabled.")
    return key


def _build_email_html(
    subject: str,
    job_type: str,
    results: dict[str, Any],
    timestamp: str,
) -> str:
    """Build a beautiful, witty HTML email digest from analysis results."""

    # Build league result sections
    league_sections = ""
    for league_name, data in results.items():
        league_sections += _render_league_section(league_name, data, job_type)

    # Pick a witty tagline based on job type
    taglines = {
        "weekly_analysis": "📋 Your weekly intel drop just landed. Time to separate the pretenders from the contenders.",
        "sunday_pregame": "🚨 GAME DAY. Inactives are rolling in. Last call to swap before kickoff.",
        "lineup_query": "🎯 You asked, the algorithm answered. Here's who's starting and who's riding pine.",
        "trade_eval": "⚖️ Trade desk is open. Let's see if this deal passes the smell test.",
    }
    tagline = taglines.get(job_type, "🏈 Fresh analysis from your AI-powered fantasy war room.")

    # Pick a random roast-style sign-off
    signoffs = [
        "Now go set that lineup before you forget like you forgot to pick up the Chargers D last week. 💀",
        "Remember: the waiver wire is where championships are won. Your draft picks just get you the invitation. 🎟️",
        "Trust the process. Or don't. Either way, I'll be here judging your bench. 👀",
        "May your opponent's QB throw 3 picks and your flex score 30. 🙏",
        "This has been your daily dose of fantasy wisdom. Go touch grass (after setting your lineup). 🌿",
        "Your roster is looking sharper than a fresh pair of cleats. Don't mess it up. 👟",
        "If you win this week, you're welcome. If you lose, you clearly ignored my advice. 😤",
    ]
    # Deterministic pick based on day of year
    signoff = signoffs[datetime.now().timetuple().tm_yday % len(signoffs)]

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; background-color: #0f172a; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
  <div style="max-width: 640px; margin: 0 auto; padding: 24px 16px;">

    <!-- Header -->
    <div style="text-align: center; padding: 24px 0 16px 0; border-bottom: 2px solid #d9381e;">
      <h1 style="color: #d9381e; font-size: 26px; margin: 0 0 4px 0;">🏈 Mad Dawg's Fantasy Intel</h1>
      <p style="color: #94a3b8; font-size: 13px; margin: 0 0 8px 0;">{subject}</p>
      <span style="display: inline-block; background: #1e293b; color: #38bdf8; font-size: 11px; padding: 4px 12px; border-radius: 9999px; border: 1px solid #334155;">🕒 Generated: {timestamp}</span>
    </div>

    <!-- Tagline -->
    <div style="background: linear-gradient(135deg, #1e293b, #0f172a); border-left: 4px solid #38bdf8; padding: 16px; margin: 20px 0; border-radius: 0 8px 8px 0;">
      <p style="color: #e2e8f0; font-size: 15px; margin: 0; font-style: italic;">{tagline}</p>
    </div>

    <!-- League Results -->
    {league_sections}

    <!-- Sign-off -->
    <div style="text-align: center; padding: 24px 0; margin-top: 16px; border-top: 1px solid #334155;">
      <p style="color: #94a3b8; font-size: 13px; font-style: italic; margin: 0 0 12px 0;">{signoff}</p>
      <p style="color: #475569; font-size: 11px; margin: 0;">
        Sent by <strong style="color: #38bdf8;">Fantasy Football Agent</strong> • Powered by Gemini 2.5 Pro<br>
        {timestamp} • <a href="https://fantasy-agent-652912521571.us-central1.run.app" style="color: #38bdf8; text-decoration: none;">Open Command Center →</a>
      </p>
    </div>

  </div>
</body>
</html>"""


def _format_matchup_str(p: dict[str, Any]) -> str:
    """Format matchup, home/away, and kickoff date/time string for emails."""
    m_disp = p.get("matchup_display")
    g_time = p.get("game_time")
    ha = p.get("home_away")
    if not m_disp and not g_time:
        return ""
    if m_disp == "BYE" or g_time == "Bye Week":
        return "BYE WEEK"
    ha_str = f" ({'Home' if ha == 'HOME' else 'Away'})" if ha else ""
    parts = [f"{m_disp}{ha_str}" if m_disp else "", g_time or ""]
    return " • ".join(part for part in parts if part)


def _render_league_section(league_name: str, data: dict[str, Any], job_type: str) -> str:
    """Render a single league's results as an HTML section."""

    if "error" in data:
        return f"""
    <div style="background: #1e293b; border: 1px solid #ef4444; border-radius: 10px; padding: 20px; margin: 16px 0;">
      <h2 style="color: #ef4444; font-size: 18px; margin: 0 0 8px 0;">❌ {league_name}</h2>
      <p style="color: #f87171; font-size: 14px; margin: 0;">Something went sideways: {data['error']}</p>
      <p style="color: #94a3b8; font-size: 12px; margin: 8px 0 0 0;">Don't panic — this usually fixes itself. Check the Command Center for details.</p>
    </div>"""

    # Generic message (e.g., "no routine for this day")
    if "message" in data and len(data) <= 2:
        return f"""
    <div style="background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 20px; margin: 16px 0;">
      <h2 style="color: #38bdf8; font-size: 18px; margin: 0 0 8px 0;">📌 {league_name}</h2>
      <p style="color: #cbd5e1; font-size: 14px; margin: 0;">{data['message']}</p>
    </div>"""

    # Build content rows from the analysis data
    content_html = ""

    # Strategy badge
    if data.get("game_theory_strategy"):
        strategy = data["game_theory_strategy"]
        color = "#22c55e" if "FLOOR" in strategy.upper() else "#eab308" if "CEILING" in strategy.upper() else "#38bdf8"
        content_html += f"""
      <div style="margin-bottom: 14px;">
        <span style="background: {color}; color: #0f172a; padding: 4px 12px; border-radius: 6px; font-size: 12px; font-weight: bold;">⚡ {strategy}</span>
      </div>"""

    # Opponent Matchup & Strategy Card
    if data.get("opponent_name"):
        opp_name = data["opponent_name"]
        opp_proj = data.get("opponent_projected_total")
        opp_act = data.get("opponent_actual_total")
        my_proj = data.get("projected_total_points")
        my_act = data.get("actual_total_points")
        opp_breakdown = data.get("opponent_matchup_breakdown")

        my_score_str = f"{my_act:.1f} pts (Live)" if (my_act is not None and my_act > 0) else (f"{my_proj:.1f} Proj" if my_proj else "--")
        opp_score_str = f"{opp_act:.1f} pts (Live)" if (opp_act is not None and opp_act > 0) else (f"{opp_proj:.1f} Proj" if opp_proj else "--")

        content_html += f"""
      <div style="background: #0f172a; border: 1px solid #a855f7; border-radius: 8px; padding: 12px 14px; margin-bottom: 14px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
          <strong style="color: #c084fc; font-size: 13px; text-transform: uppercase;">⚔️ Matchup Opponent: {opp_name}</strong>
          <span style="color: #cbd5e1; font-size: 12px;">You: <strong style="color: #38bdf8;">{my_score_str}</strong> vs Opp: <strong style="color: #f43f5e;">{opp_score_str}</strong></span>
        </div>
        {f'<div style="color: #cbd5e1; font-size: 12px; line-height: 1.4; margin-top: 4px;">🛡️ <strong>Coach\'s Counter-Strategy:</strong> {opp_breakdown}</div>' if opp_breakdown else ''}
      </div>"""

    # Post-Game Film Room & Weekly Recap
    if data.get("coach_game_summary"):
        result = data.get("result", "FINAL")
        badge_color = "#22c55e" if result == "WIN" else "#ef4444" if result == "LOSS" else "#38bdf8"
        score_margin = data.get("score_margin", 0.0)
        margin_sign = "+" if score_margin >= 0 else ""

        game_balls_html = ""
        for gb in data.get("game_balls", []):
            game_balls_html += f"""
          <div style="background: #0f172a; border-left: 3px solid #22c55e; padding: 6px 10px; margin-bottom: 6px; font-size: 12px;">
            <strong style="color: #86efac;">{gb.get('player_name')} ({gb.get('position')}):</strong> {gb.get('actual_points', 0):.1f} pts (Proj: {gb.get('projected_points', 0):.1f})
            <div style="color: #cbd5e1; margin-top: 2px;">{gb.get('verdict_comment', '')}</div>
          </div>"""

        missed_html = ""
        for mo in data.get("missed_opportunities", []):
            missed_html += f"""
          <div style="background: #0f172a; border-left: 3px solid #f59e0b; padding: 6px 10px; margin-bottom: 6px; font-size: 12px;">
            <strong style="color: #fde047;">Bench {mo.get('bench_player')} ({mo.get('bench_points', 0):.1f})</strong> outscored <strong style="color: #fca5a5;">{mo.get('started_player')} ({mo.get('starter_points', 0):.1f})</strong> (+{mo.get('points_differential', 0):.1f} pts left on bench)
            <div style="color: #cbd5e1; margin-top: 2px;">💡 {mo.get('lesson', '')}</div>
          </div>"""

        lessons_html = "".join(f"<li style='margin-bottom: 4px;'>{lesson_item}</li>" for lesson_item in data.get("lessons_learned", []))
        priorities_html = "".join(f"<li style='margin-bottom: 4px;'>{prio_item}</li>" for prio_item in data.get("next_week_priorities", []))

        content_html += f"""
      <div style="background: rgba(255,255,255,0.03); border: 1px solid #334155; border-radius: 8px; padding: 14px; margin-bottom: 16px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
          <span style="background: {badge_color}; color: #000; font-weight: bold; font-size: 11px; padding: 3px 8px; border-radius: 4px;">{result} ({margin_sign}{score_margin:.1f} PTS)</span>
          <span style="color: #94a3b8; font-size: 12px;">You: <strong>{data.get('user_score', 0):.1f}</strong> vs {data.get('opponent_team_name')}: <strong>{data.get('opponent_score', 0):.1f}</strong> (Optimal: <strong>{data.get('optimal_lineup_points', 0):.1f}</strong>)</span>
        </div>
        <div style="background: #0f172a; border-left: 4px solid #38bdf8; padding: 10px 12px; margin-bottom: 12px; font-size: 13px; color: #e2e8f0; line-height: 1.4;">
          <strong>🎙️ Head Coach Post-Game:</strong> {data.get('coach_game_summary', '')}
        </div>
        {f'<h4 style="color: #22c55e; font-size: 12px; text-transform: uppercase; margin: 10px 0 6px 0;">🏆 Game Balls</h4>{game_balls_html}' if game_balls_html else ''}
        {f'<h4 style="color: #f59e0b; font-size: 12px; text-transform: uppercase; margin: 10px 0 6px 0;">🤦 Points Left on Bench</h4>{missed_html}' if missed_html else ''}
        {f'<h4 style="color: #38bdf8; font-size: 12px; text-transform: uppercase; margin: 10px 0 6px 0;">📋 Film Room Lessons</h4><ul style="margin: 0; padding-left: 18px; color: #cbd5e1; font-size: 12px;">{lessons_html}</ul>' if lessons_html else ''}
        {f'<h4 style="color: #a855f7; font-size: 12px; text-transform: uppercase; margin: 10px 0 6px 0;">🎯 Next Week Priorities</h4><ul style="margin: 0; padding-left: 18px; color: #cbd5e1; font-size: 12px;">{priorities_html}</ul>' if priorities_html else ''}
      </div>"""

    # 0. Critical Starting Lineup Holes & Multi-Pathway Fixes
    hole_alerts = data.get("lineup_hole_alerts") or []
    if hole_alerts:
        hole_cards = ""
        for h in hole_alerts:
            slot = h.get("slot") if isinstance(h, dict) else getattr(h, "slot", "SLOT")
            status = h.get("current_status") if isinstance(h, dict) else getattr(h, "current_status", "UNKNOWN")
            player = h.get("current_player_name") if isinstance(h, dict) else getattr(h, "current_player_name", None)
            player_str = f"{player} ({status})" if player else f"EMPTY / UNFILLED ({status})"

            bench = h.get("bench_recommendation") if isinstance(h, dict) else getattr(h, "bench_recommendation", None)
            waiver = h.get("waiver_recommendation") if isinstance(h, dict) else getattr(h, "waiver_recommendation", None)
            trade = h.get("trade_recommendation") if isinstance(h, dict) else getattr(h, "trade_recommendation", None)

            bench_html = ""
            if bench:
                bench_str = bench if isinstance(bench, str) else bench.get("action_note", str(bench))
                is_exhausted = "exhausted" in bench_str.lower() or "no healthy" in bench_str.lower()
                border_color = "#ef4444" if is_exhausted else "#22c55e"
                bg_color = "rgba(239, 68, 68, 0.12)" if is_exhausted else "rgba(34, 197, 94, 0.12)"
                label_color = "#ef4444" if is_exhausted else "#22c55e"
                label_text = "⚠️ Tier 1 (Bench Depth Exhausted):" if is_exhausted else "🟢 Tier 1 (Internal Bench Fix):"
                bench_html = f"""
            <div style="background: {bg_color}; border-left: 3px solid {border_color}; padding: 8px 10px; border-radius: 0 4px 4px 0; margin-bottom: 6px;">
              <span style="color: {label_color}; font-weight: bold; font-size: 11px; text-transform: uppercase;">{label_text}</span>
              <div style="color: #f8fafc; font-size: 12px; margin-top: 2px;">{bench_str}</div>
            </div>"""

            waiver_html = ""
            if waiver:
                waiver_str = waiver if isinstance(waiver, str) else waiver.get("action_note", str(waiver))
                waiver_html = f"""
            <div style="background: rgba(56, 189, 248, 0.12); border-left: 3px solid #38bdf8; padding: 8px 10px; border-radius: 0 4px 4px 0; margin-bottom: 6px;">
              <span style="color: #38bdf8; font-weight: bold; font-size: 11px; text-transform: uppercase;">🔵 Tier 2 (Waiver Wire Pickup):</span>
              <div style="color: #f8fafc; font-size: 12px; margin-top: 2px;">{waiver_str}</div>
            </div>"""

            trade_html = ""
            if trade:
                trade_str = trade if isinstance(trade, str) else trade.get("rationale", str(trade))
                trade_html = f"""
            <div style="background: rgba(168, 85, 247, 0.12); border-left: 3px solid #a855f7; padding: 8px 10px; border-radius: 0 4px 4px 0; margin-bottom: 6px;">
              <span style="color: #c084fc; font-weight: bold; font-size: 11px; text-transform: uppercase;">🟣 Tier 3 (Proactive Trade Solution):</span>
              <div style="color: #f8fafc; font-size: 12px; margin-top: 2px;">{trade_str}</div>
            </div>"""

            hole_cards += f"""
          <div style="background: #0f172a; border: 1px solid #ef4444; border-radius: 6px; padding: 12px; margin-bottom: 10px;">
            <div style="margin-bottom: 8px;">
              <span style="background: #ef4444; color: #ffffff; font-size: 10px; font-weight: bold; padding: 2px 6px; border-radius: 3px;">HOLE DETECTED</span>
              <span style="color: #f87171; font-weight: bold; font-size: 13px; margin-left: 6px;">Slot {slot}: {player_str}</span>
            </div>
            {bench_html}
            {waiver_html}
            {trade_html}
          </div>"""

        content_html += f"""
      <div style="background: rgba(239, 68, 68, 0.15); border: 2px solid #ef4444; border-radius: 8px; padding: 14px; margin-bottom: 16px;">
        <h3 style="color: #ef4444; margin: 0 0 6px 0; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px;">🚨 Emergency Starting Lineup Alert — Action Required!</h3>
        <p style="color: #fecaca; font-size: 12px; margin: 0 0 10px 0;">
          The following starting slots have no active/healthy starter due to injury, suspension, or bye week. Here are your 3-tier solutions:
        </p>
        {hole_cards}
      </div>"""

    # 1. Action Checklist & Lineup Status
    if data.get("current_lineup"):
        has_swaps = bool(data.get("actionable_swaps"))
        has_vacant = bool(data.get("vacant_slots"))
        has_holes = bool(data.get("lineup_hole_alerts"))

        if not has_swaps and not has_vacant and not has_holes:
            content_html += """
      <div style="background: rgba(34, 197, 94, 0.15); border-left: 4px solid #22c55e; border-radius: 0 6px 6px 0; padding: 12px 14px; margin-bottom: 14px;">
        <h4 style="color: #22c55e; margin: 0 0 4px 0; font-size: 13px; text-transform: uppercase;">✅ Lineup Status: 100% Optimal</h4>
        <p style="color: #bbf7d0; font-size: 12px; margin: 0;">All your current ESPN starters are confirmed optimal. No moves needed before kickoff!</p>
      </div>"""
        else:
            if has_vacant:
                vacant_items = "".join(f"<li style='margin-bottom: 4px; font-weight: bold;'>Slot {s} is currently UNFILLED</li>" for s in data["vacant_slots"])
                content_html += f"""
      <div style="background: rgba(239, 68, 68, 0.2); border-left: 4px solid #ef4444; border-radius: 0 6px 6px 0; padding: 12px 14px; margin-bottom: 14px;">
        <h4 style="color: #ef4444; margin: 0 0 6px 0; font-size: 13px; text-transform: uppercase;">🚨 Vacant Starting Slots Detected on ESPN</h4>
        <ul style="margin: 0; padding-left: 18px; color: #fecaca; font-size: 12px;">{vacant_items}</ul>
      </div>"""
            if has_swaps:
                swaps_items = "".join(f"<li style='margin-bottom: 4px;'>{swap}</li>" for swap in data["actionable_swaps"])
                content_html += f"""
      <div style="background: rgba(234, 179, 8, 0.15); border-left: 4px solid #eab308; border-radius: 0 6px 6px 0; padding: 12px 14px; margin-bottom: 14px;">
        <h4 style="color: #eab308; margin: 0 0 6px 0; font-size: 13px; text-transform: uppercase;">⚡ Recommended Lineup Swaps on ESPN</h4>
        <ul style="margin: 0; padding-left: 18px; color: #fef08a; font-size: 12px;">{swaps_items}</ul>
      </div>"""

        # 2. Current Starting Lineup (On ESPN As-Is)
        lineup_rows = ""
        for p in data["current_lineup"]:
            name = p.get("player_name", "Unknown")
            pos = p.get("position", "?")
            slot = p.get("current_slot", pos)
            team = p.get("team", "?")
            proj = p.get("projected_points", 0)
            act_pts = p.get("actual_points")
            has_played = p.get("has_played", False)
            act = p.get("action", "")
            act_label = p.get("action_label", "START")
            act_detail = p.get("action_detail", "")
            m_str = _format_matchup_str(p)

            if has_played:
                badge_style = "color: #38bdf8; background: rgba(56, 189, 248, 0.2); font-weight: bold;"
                actual_cell = f'<td style="padding: 8px; border-bottom: 1px solid #334155; color: #38bdf8; font-weight: bold; font-size: 13px;">🏁 {act_pts:.1f}</td>'
            elif act == "KEEP_STARTING":
                badge_style = "color: #22c55e; background: rgba(34, 197, 94, 0.15);"
                actual_cell = '<td style="padding: 8px; border-bottom: 1px solid #334155; color: #64748b; font-size: 12px;">--</td>'
            else:
                badge_style = "color: #ef4444; background: rgba(239, 68, 68, 0.2); font-weight: bold;"
                actual_cell = '<td style="padding: 8px; border-bottom: 1px solid #334155; color: #64748b; font-size: 12px;">--</td>'

            lineup_rows += f"""
          <tr>
            <td style="padding: 8px; border-bottom: 1px solid #334155; color: #38bdf8; font-weight: bold; font-size: 13px;">{slot}</td>
            <td style="padding: 8px; border-bottom: 1px solid #334155; color: #f8fafc; font-weight: bold; font-size: 13px;">{name} <span style="color:#94a3b8; font-weight:normal; font-size:11px;">({team})</span>{f'<br><span style="color:#38bdf8; font-size:11px; font-weight:normal;">🗓️ {m_str}</span>' if m_str else ''}</td>
            {actual_cell}
            <td style="padding: 8px; border-bottom: 1px solid #334155; color: #22c55e; font-weight: bold; font-size: 13px;">{proj}</td>
            <td style="padding: 8px; border-bottom: 1px solid #334155; font-size: 11px;"><span style="{badge_style} padding: 2px 6px; border-radius: 4px;">{act_label}</span></td>
            <td style="padding: 8px; border-bottom: 1px solid #334155; color: #cbd5e1; font-size: 12px;">{act_detail}</td>
          </tr>"""

        content_html += f"""
      <h3 style="color: #38bdf8; font-size: 14px; margin: 14px 0 8px 0; text-transform: uppercase;">📋 Current Starting Lineup (On ESPN As-Is)</h3>
      <table style="width: 100%; border-collapse: collapse; margin-bottom: 14px;">
        <thead>
          <tr>
            <th style="padding: 8px; border-bottom: 2px solid #38bdf8; color: #94a3b8; text-align: left; font-size: 11px; text-transform: uppercase;">Slot</th>
            <th style="padding: 8px; border-bottom: 2px solid #38bdf8; color: #94a3b8; text-align: left; font-size: 11px; text-transform: uppercase;">Player</th>
            <th style="padding: 8px; border-bottom: 2px solid #38bdf8; color: #94a3b8; text-align: left; font-size: 11px; text-transform: uppercase;">Actual</th>
            <th style="padding: 8px; border-bottom: 2px solid #38bdf8; color: #94a3b8; text-align: left; font-size: 11px; text-transform: uppercase;">Proj</th>
            <th style="padding: 8px; border-bottom: 2px solid #38bdf8; color: #94a3b8; text-align: left; font-size: 11px; text-transform: uppercase;">Verdict</th>
            <th style="padding: 8px; border-bottom: 2px solid #38bdf8; color: #94a3b8; text-align: left; font-size: 11px; text-transform: uppercase;">Analysis & Advice</th>
          </tr>
        </thead>
        <tbody>{lineup_rows}</tbody>
      </table>"""

        # 3. Current Bench (On ESPN As-Is)
        bench_rows = ""
        for p in data.get("current_bench", []):
            name = p.get("player_name", "Unknown")
            pos = p.get("position", "?")
            team = p.get("team", "?")
            proj = p.get("projected_points", 0)
            act_pts = p.get("actual_points")
            has_played = p.get("has_played", False)
            act = p.get("action", "")
            act_label = p.get("action_label", "BENCH")
            act_detail = p.get("action_detail", "")
            m_str = _format_matchup_str(p)

            if has_played:
                badge_style = "color: #38bdf8; background: rgba(56, 189, 248, 0.2); font-weight: bold;"
                actual_cell = f'<td style="padding: 8px; border-bottom: 1px solid #334155; color: #38bdf8; font-weight: bold; font-size: 13px;">🏁 {act_pts:.1f}</td>'
            elif act == "PROMOTE_TO_START":
                badge_style = "color: #f59e0b; background: rgba(245, 158, 11, 0.2); font-weight: bold;"
                actual_cell = '<td style="padding: 8px; border-bottom: 1px solid #334155; color: #64748b; font-size: 12px;">--</td>'
            else:
                badge_style = "color: #94a3b8; background: rgba(148, 163, 184, 0.15);"
                actual_cell = '<td style="padding: 8px; border-bottom: 1px solid #334155; color: #64748b; font-size: 12px;">--</td>'

            bench_rows += f"""
          <tr>
            <td style="padding: 8px; border-bottom: 1px solid #334155; color: #94a3b8; font-weight: bold; font-size: 13px;">{pos}</td>
            <td style="padding: 8px; border-bottom: 1px solid #334155; color: #cbd5e1; font-size: 13px;">{name} <span style="color:#64748b; font-size:11px;">({team})</span>{f'<br><span style="color:#94a3b8; font-size:11px; font-weight:normal;">🗓️ {m_str}</span>' if m_str else ''}</td>
            {actual_cell}
            <td style="padding: 8px; border-bottom: 1px solid #334155; color: #94a3b8; font-size: 13px;">{proj}</td>
            <td style="padding: 8px; border-bottom: 1px solid #334155; font-size: 11px;"><span style="{badge_style} padding: 2px 6px; border-radius: 4px;">{act_label}</span></td>
            <td style="padding: 8px; border-bottom: 1px solid #334155; color: #94a3b8; font-size: 12px;">{act_detail}</td>
          </tr>"""

        content_html += f"""
      <h3 style="color: #94a3b8; font-size: 14px; margin: 16px 0 8px 0; text-transform: uppercase;">⏸️ Current Bench (On ESPN As-Is)</h3>
      <table style="width: 100%; border-collapse: collapse; margin-bottom: 14px;">
        <thead>
          <tr>
            <th style="padding: 8px; border-bottom: 2px solid #64748b; color: #94a3b8; text-align: left; font-size: 11px; text-transform: uppercase;">Pos</th>
            <th style="padding: 8px; border-bottom: 2px solid #64748b; color: #94a3b8; text-align: left; font-size: 11px; text-transform: uppercase;">Player</th>
            <th style="padding: 8px; border-bottom: 2px solid #64748b; color: #94a3b8; text-align: left; font-size: 11px; text-transform: uppercase;">Actual</th>
            <th style="padding: 8px; border-bottom: 2px solid #64748b; color: #94a3b8; text-align: left; font-size: 11px; text-transform: uppercase;">Proj</th>
            <th style="padding: 8px; border-bottom: 2px solid #64748b; color: #94a3b8; text-align: left; font-size: 11px; text-transform: uppercase;">Verdict</th>
            <th style="padding: 8px; border-bottom: 2px solid #64748b; color: #94a3b8; text-align: left; font-size: 11px; text-transform: uppercase;">Analysis & Advice</th>
          </tr>
        </thead>
        <tbody>{bench_rows}</tbody>
      </table>"""

    else:
        # Fallback to recommended starters table if current_lineup not present
        if data.get("recommended_starters"):
            rows = ""
            for p in data["recommended_starters"]:
                name = p.get("player_name", "Unknown")
                pos = p.get("position", "?")
                team = p.get("team", "?")
                proj = p.get("projected_points", 0)
                reasoning = p.get("reasoning", "")
                game_note = p.get("game_script_note", "")
                full_reason = f"{reasoning} ({game_note})" if game_note else reasoning
                rows += f"""
          <tr>
            <td style="padding: 8px; border-bottom: 1px solid #334155; color: #38bdf8; font-weight: bold; font-size: 13px;">{pos}</td>
            <td style="padding: 8px; border-bottom: 1px solid #334155; color: #f8fafc; font-weight: bold; font-size: 13px;">{name}</td>
            <td style="padding: 8px; border-bottom: 1px solid #334155; color: #94a3b8; font-size: 13px;">{team}</td>
            <td style="padding: 8px; border-bottom: 1px solid #334155; color: #22c55e; font-weight: bold; font-size: 13px;">{proj}</td>
            <td style="padding: 8px; border-bottom: 1px solid #334155; color: #94a3b8; font-size: 12px;">{full_reason}</td>
          </tr>"""
            content_html += f"""
      <h3 style="color: #22c55e; font-size: 14px; margin: 14px 0 8px 0; text-transform: uppercase;">🟢 Start 'Em (Optimal Lineup)</h3>
      <table style="width: 100%; border-collapse: collapse; margin-bottom: 14px;">
        <thead>
          <tr>
            <th style="padding: 8px; border-bottom: 2px solid #22c55e; color: #94a3b8; text-align: left; font-size: 11px; text-transform: uppercase;">Pos</th>
            <th style="padding: 8px; border-bottom: 2px solid #22c55e; color: #94a3b8; text-align: left; font-size: 11px; text-transform: uppercase;">Player</th>
            <th style="padding: 8px; border-bottom: 2px solid #22c55e; color: #94a3b8; text-align: left; font-size: 11px; text-transform: uppercase;">Team</th>
            <th style="padding: 8px; border-bottom: 2px solid #22c55e; color: #94a3b8; text-align: left; font-size: 11px; text-transform: uppercase;">Proj</th>
            <th style="padding: 8px; border-bottom: 2px solid #22c55e; color: #94a3b8; text-align: left; font-size: 11px; text-transform: uppercase;">Intel & Matchup</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>"""

        if data.get("bench_players"):
            bench_rows = ""
            for p in data["bench_players"]:
                name = p.get("player_name", "Unknown")
                pos = p.get("position", "?")
                team = p.get("team", "?")
                proj = p.get("projected_points", 0)
                reasoning = p.get("reasoning", "")
                bench_rows += f"""
          <tr>
            <td style="padding: 8px; border-bottom: 1px solid #334155; color: #ef4444; font-weight: bold; font-size: 13px;">{pos}</td>
            <td style="padding: 8px; border-bottom: 1px solid #334155; color: #cbd5e1; font-size: 13px;">{name}</td>
            <td style="padding: 8px; border-bottom: 1px solid #334155; color: #94a3b8; font-size: 13px;">{team}</td>
            <td style="padding: 8px; border-bottom: 1px solid #334155; color: #94a3b8; font-size: 13px;">{proj}</td>
            <td style="padding: 8px; border-bottom: 1px solid #334155; color: #94a3b8; font-size: 12px;">{reasoning}</td>
          </tr>"""
            content_html += f"""
      <h3 style="color: #ef4444; font-size: 14px; margin: 16px 0 8px 0; text-transform: uppercase;">🔴 Sit 'Em (Bench Options)</h3>
      <table style="width: 100%; border-collapse: collapse; margin-bottom: 14px;">
        <thead>
          <tr>
            <th style="padding: 8px; border-bottom: 2px solid #ef4444; color: #94a3b8; text-align: left; font-size: 11px; text-transform: uppercase;">Pos</th>
            <th style="padding: 8px; border-bottom: 2px solid #ef4444; color: #94a3b8; text-align: left; font-size: 11px; text-transform: uppercase;">Player</th>
            <th style="padding: 8px; border-bottom: 2px solid #ef4444; color: #94a3b8; text-align: left; font-size: 11px; text-transform: uppercase;">Team</th>
            <th style="padding: 8px; border-bottom: 2px solid #ef4444; color: #94a3b8; text-align: left; font-size: 11px; text-transform: uppercase;">Proj</th>
            <th style="padding: 8px; border-bottom: 2px solid #ef4444; color: #94a3b8; text-align: left; font-size: 11px; text-transform: uppercase;">Why Sit</th>
          </tr>
        </thead>
        <tbody>{bench_rows}</tbody>
      </table>"""


    # Key start/sit dilemmas & flex calls
    if data.get("key_flex_decisions"):
        dilemma_items = "".join(f"<li style='margin-bottom: 6px; color: #e2e8f0; font-size: 13px;'>{d}</li>" for d in data["key_flex_decisions"])
        content_html += f"""
      <div style="background: #0f172a; border-left: 4px solid #eab308; border-radius: 0 6px 6px 0; padding: 12px 14px; margin-bottom: 14px;">
        <h4 style="color: #eab308; margin: 0 0 8px 0; font-size: 13px; text-transform: uppercase;">⚖️ Key Start/Sit Dilemmas & Flex Decisions</h4>
        <ul style="margin: 0; padding-left: 18px;">{dilemma_items}</ul>
      </div>"""

    # Waiver wire picks (or Stand Pat verdict)
    is_waiver_data = (
        data.get("coach_verdict") in ("STAND_PAT", "EXECUTE_CLAIMS")
        or "targets" in data
        or "priority_claims" in data
        or "overall_waiver_strategy" in data
    )
    if is_waiver_data:
        is_stand_pat = (
            data.get("coach_verdict") == "STAND_PAT"
            or ("targets" in data and not data.get("targets"))
            or ("overall_waiver_strategy" in data and not data.get("targets") and not data.get("priority_claims"))
        )
        if is_stand_pat:
            reason = (
                data.get("stand_pat_reasoning")
                or data.get("overall_waiver_strategy")
                or "Your active starters are locked in and your bench provides crucial high-upside depth. None of the available waiver options represent a meaningful upgrade."
            )
            content_html += f"""
      <div style="background: rgba(34, 197, 94, 0.12); border-left: 4px solid #22c55e; border-radius: 0 6px 6px 0; padding: 12px 14px; margin: 14px 0;">
        <h4 style="color: #22c55e; margin: 0 0 6px 0; font-size: 13px; text-transform: uppercase;">🛡️ Coach's Verdict: Stand Pat (No Moves Recommended)</h4>
        <p style="color: #e2e8f0; font-size: 12px; margin: 0 0 6px 0; line-height: 1.4;">{reason}</p>
        <p style="color: #86efac; font-size: 11px; margin: 0;">💡 Preserving rolling waiver priority for high-impact injury breakouts later in the season.</p>
      </div>"""
        else:
            claims_html = ""
            if data.get("overall_waiver_strategy"):
                claims_html += f"""<p style="color: #cbd5e1; font-size: 12px; margin: 0 0 8px 0;"><em>{data['overall_waiver_strategy']}</em></p>"""

            # Handle either targets or priority_claims
            claims_list = data.get("targets") or data.get("priority_claims") or []
            for claim in claims_list:
                add_name = claim.get("player_name") or claim.get("add_player", "?")
                drop_name = claim.get("recommended_drop") or claim.get("drop_player", "None")
                reason = claim.get("reasoning", "")
                prio = claim.get("priority", "CLAIM")
                pos = claim.get("position", "")
                pos_str = f" ({pos})" if pos else ""
                claims_html += f"""
        <div style="background: #0f172a; padding: 10px 14px; border-radius: 6px; margin-bottom: 8px; border-left: 3px solid #eab308;">
          <p style="margin: 0; font-size: 13px;">
            <span style="color: #22c55e; font-weight: bold;">⬆️ ADD {add_name}{pos_str}</span>
            <span style="color: #94a3b8;"> → </span>
            <span style="color: #ef4444; font-weight: bold;">⬇️ DROP {drop_name}</span>
            <span style="color: #eab308; font-size: 11px; margin-left: 6px; font-weight: bold;">[{prio}]</span>
          </p>
          <p style="margin: 4px 0 0 0; color: #94a3b8; font-size: 12px;">{reason}</p>
        </div>"""
            content_html += f"""
      <h3 style="color: #eab308; font-size: 15px; margin: 14px 0 8px 0;">🔄 Waiver Wire Moves</h3>
      {claims_html}"""

    # Proactive trade proposals (or Hold Roster verdict)
    is_trade_finder_data = (
        data.get("coach_verdict") in ("HOLD_ROSTER", "PROPOSE_TRADES")
        or "proposals" in data
        or "market_overview" in data
    )
    if is_trade_finder_data:
        is_hold_roster = (
            data.get("coach_verdict") == "HOLD_ROSTER"
            or ("market_overview" in data and not data.get("proposals"))
        )
        if is_hold_roster:
            reason = (
                data.get("hold_roster_reasoning")
                or data.get("market_overview")
                or "Your starting lineup is strong and your bench provides crucial positional depth. No opposing teams currently offer a trade package that improves your starting lineup without compromising essential depth."
            )
            content_html += f"""
      <div style="background: rgba(56, 189, 248, 0.12); border-left: 4px solid #38bdf8; border-radius: 0 6px 6px 0; padding: 12px 14px; margin: 14px 0;">
        <h4 style="color: #38bdf8; margin: 0 0 6px 0; font-size: 13px; text-transform: uppercase;">🛡️ Coach's Verdict: Hold Roster (Stand Pat on Trades)</h4>
        <p style="color: #e2e8f0; font-size: 12px; margin: 0 0 6px 0; line-height: 1.4;">{reason}</p>
        <p style="color: #7dd3fc; font-size: 11px; margin: 0;">💡 Your starting lineup is strong and depth is preserved. No lateral moves.</p>
      </div>"""
        elif data.get("proposals"):
            proposals_html = ""
            if data.get("market_overview"):
                proposals_html += f"""<p style="color: #cbd5e1; font-size: 12px; margin: 0 0 10px 0;"><em>{data['market_overview']}</em></p>"""
            for tp in data["proposals"]:
                tgt_team = tp.get("target_team_name", "Opponent")
                tgt_mgr = tp.get("target_manager", "")
                mgr_str = f" ({tgt_mgr})" if tgt_mgr else ""
                giving = ", ".join(tp.get("giving_players", []))
                recving = ", ".join(tp.get("receiving_players", []))
                vorp_gain = tp.get("net_vorp_gain", 0.0)
                upgrade = tp.get("your_lineup_upgrade", "")
                why_opp = tp.get("why_target_accepts", "")
                pitch = tp.get("negotiation_pitch", "")
                proposals_html += f"""
        <div style="background: #0f172a; border-left: 4px solid #38bdf8; border-radius: 0 8px 8px 0; padding: 12px 14px; margin-bottom: 10px;">
          <div style="margin-bottom: 6px;">
            <strong style="color: #f8fafc; font-size: 14px;">🤝 Trade with {tgt_team}{mgr_str}</strong>
            <span style="color: #22c55e; font-weight: bold; font-size: 12px; margin-left: 8px;">+{vorp_gain:+.1f} Weekly VORP</span>
          </div>
          <p style="margin: 4px 0; font-size: 13px;">
            <span style="color: #ef4444; font-weight: bold;">Give:</span> <span style="color: #e2e8f0;">{giving}</span>
            <span style="color: #94a3b8; margin: 0 6px;">➔</span>
            <span style="color: #22c55e; font-weight: bold;">Receive:</span> <span style="color: #e2e8f0;">{recving}</span>
          </p>
          <p style="margin: 4px 0; color: #cbd5e1; font-size: 12px;"><strong style="color: #38bdf8;">Lineup Upgrade:</strong> {upgrade}</p>
          <p style="margin: 4px 0; color: #cbd5e1; font-size: 12px;"><strong style="color: #a78bfa;">Why They Accept:</strong> {why_opp}</p>
          <div style="background: #1e293b; padding: 8px 10px; border-radius: 4px; margin-top: 8px; font-size: 12px; color: #38bdf8; font-style: italic;">
            💬 <strong>Pitch:</strong> "{pitch}"
          </div>
        </div>"""
            content_html += f"""
      <h3 style="color: #38bdf8; font-size: 14px; margin: 16px 0 8px 0; text-transform: uppercase;">💡 Proactive Win-Win Trade Proposals</h3>
      {proposals_html}"""

    # Trade verdict
    if data.get("verdict"):
        verdict = data["verdict"]
        v_color = "#22c55e" if verdict == "ACCEPT" else "#ef4444" if verdict == "REJECT" else "#eab308"
        content_html += f"""
      <div style="background: #0f172a; border-left: 4px solid {v_color}; padding: 14px; border-radius: 0 8px 8px 0; margin: 14px 0;">
        <h3 style="color: {v_color}; font-size: 16px; margin: 0 0 6px 0;">VERDICT: {verdict}</h3>
        <p style="color: #cbd5e1; font-size: 14px; margin: 0;">{data.get('reasoning', '')}</p>
      </div>"""

    # Matchup preview highlights
    if data.get("win_probability") is not None:
        wp = data["win_probability"]
        wp_color = "#22c55e" if wp > 55 else "#ef4444" if wp < 45 else "#eab308"
        content_html += f"""
      <div style="text-align: center; padding: 12px; background: #0f172a; border-radius: 8px; margin: 14px 0;">
        <p style="color: #94a3b8; font-size: 12px; margin: 0;">WIN PROBABILITY</p>
        <p style="color: {wp_color}; font-size: 32px; font-weight: bold; margin: 4px 0;">{wp}%</p>
      </div>"""

    if data.get("key_matchups"):
        for km in data["key_matchups"][:3]:
            content_html += f"""
      <div style="background: #0f172a; padding: 10px 14px; border-radius: 6px; margin-bottom: 6px;">
        <p style="margin: 0; color: #f8fafc; font-size: 13px;">⚔️ {km.get('description', km)}</p>
      </div>"""

    # Fallback: if no structured fields found, dump a summary
    if not content_html:
        # Try to render any meaningful top-level keys
        summary_lines = []
        for key, val in data.items():
            if key in ("timestamp", "job", "day"):
                continue
            if isinstance(val, str):
                summary_lines.append(f"<p style='color: #cbd5e1; font-size: 14px; margin: 4px 0;'><strong style='color: #38bdf8;'>{key.replace('_', ' ').title()}:</strong> {val}</p>")
            elif isinstance(val, (int, float)):
                summary_lines.append(f"<p style='color: #cbd5e1; font-size: 14px; margin: 4px 0;'><strong style='color: #38bdf8;'>{key.replace('_', ' ').title()}:</strong> {val}</p>")
        content_html = "\n".join(summary_lines) if summary_lines else "<p style='color: #94a3b8; font-size: 14px;'>Analysis complete. Check the Command Center for full details.</p>"

    # League emoji mapping
    league_emoji = "🏆" if "PNA" in league_name.upper() else "🍪" if "CHIP" in league_name.upper() else "🏈"

    return f"""
    <div style="background: #1e293b; border: 1px solid #334155; border-radius: 10px; padding: 20px; margin: 16px 0;">
      <h2 style="color: #f8fafc; font-size: 18px; margin: 0 0 14px 0;">{league_emoji} {league_name}</h2>
      {content_html}
    </div>"""


def _subject_for_job(job_type: str, day: str | None = None) -> str:
    """Generate a witty email subject line."""
    subjects = {
        "weekly_analysis": {
            "Tuesday": "🔄 Waiver Wire Intel — Who to Snag Before Your Leaguemates Wake Up",
            "Thursday": "🏥 Thursday Injury & TNF Report — The Hospital Ward Update",
            "Friday": "📊 Friday Lineup Lock — Decisions, Decisions...",
            "Saturday": "🔍 Saturday Scouting Report — Know Your Enemy",
        },
        "sunday_pregame": "🚨 GAME DAY ALERT — Final Lineup Check Before Kickoff!",
        "lineup_query": "🎯 Your Lineup Optimization Results Are In",
        "trade_eval": "⚖️ Trade Evaluation Complete — Here's the Verdict",
    }

    if job_type == "weekly_analysis" and day:
        return subjects["weekly_analysis"].get(
            day,
            f"📋 {day} Analysis — Your Fantasy War Room Update",
        )
    return subjects.get(job_type, "🏈 Fantasy Football Agent — Analysis Complete")


def send_digest_email(
    job_type: str,
    results: dict[str, Any],
    day: str | None = None,
) -> bool:
    """Send an HTML digest email via SendGrid.

    Args:
        job_type: Type of analysis run (weekly_analysis, sunday_pregame, etc.)
        results: Dictionary of league results keyed by league short_name.
        day: Optional day name for weekly analysis subject lines.

    Returns:
        True if email sent successfully, False otherwise.
    """
    api_key = _get_sendgrid_key()
    if not api_key:
        return False

    subject = _subject_for_job(job_type, day)
    timestamp = datetime.now().strftime("%B %d, %Y at %I:%M %p ET")

    html_body = _build_email_html(
        subject=subject,
        job_type=job_type,
        results=results,
        timestamp=timestamp,
    )

    payload = {
        "personalizations": [
            {
                "to": [{"email": RECIPIENT_EMAIL, "name": RECIPIENT_NAME}],
                "subject": subject,
            }
        ],
        "from": {"email": SENDER_EMAIL, "name": SENDER_NAME},
        "content": [{"type": "text/html", "value": html_body}],
        "tracking_settings": {
            "click_tracking": {"enable": False},
            "open_tracking": {"enable": False},
        },
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                SENDGRID_API_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )

        if resp.status_code in (200, 201, 202):
            logger.info("✅ Digest email sent: '%s' → %s", subject, RECIPIENT_EMAIL)
            return True
        else:
            logger.error("❌ SendGrid returned %s: %s", resp.status_code, resp.text)
            return False

    except Exception as e:
        logger.error("❌ Failed to send email: %s", e, exc_info=True)
        return False
