"""Tests for the email notification module."""

from unittest.mock import MagicMock, patch

import httpx

from src.notifications.email import (
    _build_email_html,
    _subject_for_job,
    send_digest_email,
)


class TestSubjectGeneration:
    """Tests for email subject line generation."""

    def test_tuesday_waiver_subject(self):
        subject = _subject_for_job("weekly_analysis", "Tuesday")
        assert "Waiver Wire" in subject

    def test_thursday_injury_subject(self):
        subject = _subject_for_job("weekly_analysis", "Thursday")
        assert "Injury" in subject or "TNF" in subject

    def test_sunday_pregame_subject(self):
        subject = _subject_for_job("sunday_pregame")
        assert "GAME DAY" in subject

    def test_unknown_day_fallback(self):
        subject = _subject_for_job("weekly_analysis", "Wednesday")
        assert "Wednesday" in subject

    def test_lineup_query_subject(self):
        subject = _subject_for_job("lineup_query")
        assert "Lineup" in subject


class TestEmailHtmlBuilder:
    """Tests for the HTML email body builder."""

    def test_builds_html_with_league_sections(self):
        results = {
            "PNA": {
                "game_theory_strategy": "HIGH FLOOR",
                "strategy_reasoning": "You're favored, play it safe.",
                "recommended_starters": [
                    {
                        "player_name": "Brock Purdy",
                        "position": "QB",
                        "team": "SF",
                        "projected_points": 22.4,
                        "reasoning": "Elite matchup vs Arizona",
                        "game_script_note": "Should throw a lot",
                    }
                ],
            },
            "Chips": {
                "message": "No specific routine scheduled for Monday",
            },
        }
        html = _build_email_html(
            subject="Test Subject",
            job_type="weekly_analysis",
            results=results,
            timestamp="September 7, 2026 at 01:00 PM ET",
        )

        assert "Mad Dawg" in html
        assert "Brock Purdy" in html
        assert "HIGH FLOOR" in html
        assert "PNA" in html
        assert "Chips" in html
        assert "Command Center" in html

    def test_renders_error_section(self):
        results = {"PNA": {"error": "ESPN API timeout"}}
        html = _build_email_html("Test", "weekly_analysis", results, "now")
        assert "ESPN API timeout" in html
        assert "sideways" in html

    def test_renders_waiver_claims(self):
        results = {
            "PNA": {
                "priority_claims": [
                    {
                        "add_player": "C.J. Stroud",
                        "drop_player": "Geno Smith",
                        "reasoning": "Massive upgrade at QB",
                    }
                ]
            }
        }
        html = _build_email_html("Test", "weekly_analysis", results, "now")
        assert "C.J. Stroud" in html
        assert "Geno Smith" in html
        assert "ADD" in html
        assert "DROP" in html

    def test_renders_trade_verdict(self):
        results = {
            "PNA": {
                "verdict": "ACCEPT",
                "reasoning": "You win this trade by a mile.",
            }
        }
        html = _build_email_html("Test", "trade_eval", results, "now")
        assert "ACCEPT" in html
        assert "VERDICT" in html

    def test_renders_win_probability(self):
        results = {"PNA": {"win_probability": 72, "key_matchups": []}}
        html = _build_email_html("Test", "weekly_analysis", results, "now")
        assert "72%" in html
        assert "WIN PROBABILITY" in html


class TestSendDigestEmail:
    """Tests for the synchronous email sending function."""

    def test_returns_false_when_no_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            result = send_digest_email("weekly_analysis", {"PNA": {}})
            assert result is False

    def test_sends_email_successfully(self):
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.text = ""

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post = MagicMock(return_value=mock_response)

        with (
            patch.dict("os.environ", {"SENDGRID_API_KEY": "SG.test_key_123"}),
            patch("httpx.Client", return_value=mock_client),
        ):
            result = send_digest_email(
                "weekly_analysis", {"PNA": {"message": "test"}}, day="Tuesday"
            )
            assert result is True
            assert mock_client.post.called
            call_args = mock_client.post.call_args
            assert "Bearer SG.test_key_123" in call_args.kwargs["headers"]["Authorization"]

    def test_returns_false_on_sendgrid_error(self):
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post = MagicMock(return_value=mock_response)

        with (
            patch.dict("os.environ", {"SENDGRID_API_KEY": "SG.bad_key"}),
            patch("httpx.Client", return_value=mock_client),
        ):
            result = send_digest_email("sunday_pregame", {"PNA": {}})
            assert result is False

    def test_returns_false_on_network_error(self):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post = MagicMock(side_effect=httpx.ConnectError("DNS failed"))

        with (
            patch.dict("os.environ", {"SENDGRID_API_KEY": "SG.test_key"}),
            patch("httpx.Client", return_value=mock_client),
        ):
            result = send_digest_email("weekly_analysis", {"PNA": {}})
            assert result is False

