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
    <div style="text-align: center; padding: 24px 0; border-bottom: 2px solid #d9381e;">
      <h1 style="color: #d9381e; font-size: 26px; margin: 0 0 4px 0;">🏈 Mad Dawg's Fantasy Intel</h1>
      <p style="color: #94a3b8; font-size: 13px; margin: 0;">{subject}</p>
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

    # Strategy reasoning
    if data.get("strategy_reasoning"):
        content_html += f"""
      <p style="color: #cbd5e1; font-size: 14px; margin: 0 0 14px 0; font-style: italic;">"{data['strategy_reasoning']}"</p>"""

    # Recommended starters table
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
        <tbody>{rows}
        </tbody>
      </table>"""

    # Bench players / Sit 'Em table & Injury Alert
    if data.get("bench_players"):
        bench_rows = ""
        injury_alerts = []
        for p in data["bench_players"]:
            name = p.get("player_name", "Unknown")
            pos = p.get("position", "?")
            team = p.get("team", "?")
            proj = p.get("projected_points", 0)
            reasoning = p.get("reasoning", "")

            # Identify injured/inactive players
            upper_r = reasoning.upper()
            if any(term in upper_r for term in ("OUT", "IR", "DOUBTFUL", "INACTIVE", "SUSPENDED", "QUESTIONABLE")):
                injury_alerts.append(f"<strong>{name} ({pos} - {team})</strong>: {reasoning}")

            bench_rows += f"""
          <tr>
            <td style="padding: 8px; border-bottom: 1px solid #334155; color: #ef4444; font-weight: bold; font-size: 13px;">{pos}</td>
            <td style="padding: 8px; border-bottom: 1px solid #334155; color: #cbd5e1; font-size: 13px;">{name}</td>
            <td style="padding: 8px; border-bottom: 1px solid #334155; color: #94a3b8; font-size: 13px;">{team}</td>
            <td style="padding: 8px; border-bottom: 1px solid #334155; color: #94a3b8; font-size: 13px;">{proj}</td>
            <td style="padding: 8px; border-bottom: 1px solid #334155; color: #94a3b8; font-size: 12px;">{reasoning}</td>
          </tr>"""

        if injury_alerts:
            alerts_li = "".join(f"<li style='margin-bottom: 4px;'>{a}</li>" for a in injury_alerts)
            content_html += f"""
      <div style="background: rgba(239, 68, 68, 0.15); border-left: 4px solid #ef4444; border-radius: 0 6px 6px 0; padding: 12px; margin-bottom: 14px;">
        <h4 style="color: #ef4444; margin: 0 0 6px 0; font-size: 12px; text-transform: uppercase;">🚨 Injury / Inactive Status Alerts (Do Not Start)</h4>
        <ul style="margin: 0; padding-left: 18px; color: #fca5a5; font-size: 12px;">{alerts_li}</ul>
      </div>"""

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
        <tbody>{bench_rows}
        </tbody>
      </table>"""

    # Key start/sit dilemmas & flex calls
    if data.get("key_flex_decisions"):
        dilemma_items = "".join(f"<li style='margin-bottom: 6px; color: #e2e8f0; font-size: 13px;'>{d}</li>" for d in data["key_flex_decisions"])
        content_html += f"""
      <div style="background: #0f172a; border-left: 4px solid #eab308; border-radius: 0 6px 6px 0; padding: 12px 14px; margin-bottom: 14px;">
        <h4 style="color: #eab308; margin: 0 0 8px 0; font-size: 13px; text-transform: uppercase;">⚖️ Key Start/Sit Dilemmas & Flex Decisions</h4>
        <ul style="margin: 0; padding-left: 18px;">{dilemma_items}</ul>
      </div>"""

    # Waiver wire picks
    if data.get("priority_claims"):
        claims_html = ""
        for claim in data["priority_claims"]:
            add_name = claim.get("add_player", "?")
            drop_name = claim.get("drop_player", "?")
            reason = claim.get("reasoning", "")
            claims_html += f"""
        <div style="background: #0f172a; padding: 10px 14px; border-radius: 6px; margin-bottom: 8px;">
          <p style="margin: 0; font-size: 14px;">
            <span style="color: #22c55e; font-weight: bold;">⬆️ ADD {add_name}</span>
            <span style="color: #94a3b8;"> → </span>
            <span style="color: #ef4444; font-weight: bold;">⬇️ DROP {drop_name}</span>
          </p>
          <p style="margin: 4px 0 0 0; color: #94a3b8; font-size: 12px;">{reason}</p>
        </div>"""
        content_html += f"""
      <h3 style="color: #eab308; font-size: 15px; margin: 14px 0 8px 0;">🔄 Waiver Wire Moves</h3>
      {claims_html}"""

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
