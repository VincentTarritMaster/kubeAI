"""Readable French timestamps and compact answers preserve UTC evidence."""

from datetime import datetime, timezone

import pytest

from app.config import Settings
from app.services.correlation import render as render_correlation
from app.services.incident_timeline import render as render_timeline
from app.services.presentation import full_time, local_time, missing_summary, technical_details


def entry(instant, phase, suffix):
    return {"timestamp": instant, "type": "log", "source": "Loki",
            "resource": "kubeai/demo", "observation": f"phase={phase}",
            "reference": f"Loki kubeai/demo timestamp_ns={suffix}"}


def report(timeline):
    return {"namespace": "kubeai", "pod": "demo", "start": timeline[0]["timestamp"],
            "end": timeline[-1]["timestamp"], "timezone": "UTC", "timeline": timeline,
            "current_pods": [], "missing": [], "warnings": [], "sources": {"loki": {"status": "ok"}}}


def test_swiss_time_example_and_utc_unchanged():
    source = "2026-10-19T18:28:39.725804Z"
    assert full_time(source) == ("19 octobre 2026 à 20 h 28 min 39 s "
                                 "(heure suisse — Europe/Zurich, UTC+2)")
    assert source == "2026-10-19T18:28:39.725804Z"
    assert local_time(source).astimezone(timezone.utc) == datetime(2026, 10, 19, 18, 28, 39, 725804,
                                                                    tzinfo=timezone.utc)


@pytest.mark.parametrize("instant,clock,offset", [
    ("2026-03-29T00:59:59Z", "01 h 59 min 59 s", "UTC+1"),
    ("2026-03-29T01:00:00Z", "03 h 00 min 00 s", "UTC+2"),
    ("2026-10-25T00:59:59Z", "02 h 59 min 59 s", "UTC+2"),
    ("2026-10-25T01:00:00Z", "02 h 00 min 00 s", "UTC+1"),
])
def test_swiss_summer_winter_transitions(instant, clock, offset):
    text = full_time(instant)
    assert clock in text and offset in text


def test_named_display_timezone_and_invalid_setting():
    assert "UTC+1" in full_time("2026-10-19T18:28:39Z", "Europe/London")
    with pytest.raises(ValueError, match="KUBEAI_DISPLAY_TIMEZONE"):
        Settings(display_timezone="Invalid/Nowhere")


def test_multi_day_timeline_groups_local_dates_and_keeps_every_utc_proof():
    timeline = [entry("2026-10-19T21:59:59Z", "anomaly_start", "1"),
                entry("2026-10-19T22:01:00Z", "recovered", "2")]
    answer = render_timeline(report(timeline), "Analyse IA non sollicitée.")
    visible = answer.split("## Chronologie de l'incident", 1)[1].split("<details>", 1)[0]
    assert visible.index("**19 octobre 2026 · Heure suisse (UTC+2)**") < visible.index("23:59:59")
    assert visible.index("**20 octobre 2026 · Heure suisse (UTC+2)**") < visible.index("00:01:00")
    assert visible.index("23:59:59") < visible.index("00:01:00")
    assert "2026-10-19T21:59:59Z" not in visible
    assert "2026-10-19T22:01:00Z" not in visible
    assert all(item["timestamp"] in answer and item["reference"] in answer for item in timeline)


def test_numbered_timeline_keeps_title_and_description_apart_for_markdown():
    timeline = [entry("2026-10-19T18:22:45Z", "normal", "1"),
                entry("2026-10-19T18:24:15Z", "anomaly_start", "2")]
    visible = render_timeline(report(timeline), "Sans analyse IA.").split("<details>", 1)[0]
    assert "1. **20:22:45 — Fonctionnement normal signalé**<br>\n\n   Marqueur `normal` enregistré dans Loki." in visible
    assert "2. **20:24:15 — Début d'une anomalie**<br>\n\n   Marqueur `anomaly_start` enregistré dans Loki." in visible
    assert "signaléMarqueur" not in visible


def test_dst_fallback_same_day_disambiguates_repeated_clock():
    timeline = [entry("2026-10-25T00:30:00Z", "anomaly_start", "1"),
                entry("2026-10-25T01:30:00Z", "recovered", "2")]
    visible = render_timeline(report(timeline), "Sans analyse IA.").split("<details>", 1)[0]
    assert "02:30:00 (UTC+2)" in visible
    assert "02:30:00 (UTC+1)" in visible
    assert "offset indiqué pour chaque événement" in visible


def test_correlation_proofs_disambiguate_dst_fallback_and_keep_utc_references():
    proofs = [entry("2026-10-25T00:30:00Z", "anomaly_start", "1"),
              entry("2026-10-25T01:30:00Z", "recovered", "2")]
    hypothesis = {"id": "connectivity", "description": "Association temporelle seulement.",
                  "confidence": "limitée", "supporting_observations": proofs,
                  "recommended_checks": [], "alternatives": [], "contradictions": []}
    case = {**report(proofs), "hypotheses": [hypothesis], "conclusion": "hypothèse non confirmée",
            "correlation_limits": [], "process_near_limit": False}
    answer = render_correlation(case, "Analyse déterministe.")
    visible, details = answer.split("<details>", 1)
    assert "**25 octobre 2026 · Heure suisse (offset indiqué pour chaque événement)**" in visible
    assert "02:30:00 (UTC+2)" in visible and "02:30:00 (UTC+1)" in visible
    assert visible.count("25 octobre 2026") == 1
    assert all(item["timestamp"] in details and item["reference"] in details for item in proofs)


def test_technical_evidence_escapes_untrusted_log_text():
    details = technical_details(["<script>alert('x')</script>", "2026-10-19T18:28:39Z | Loki ref"])
    assert "<script>" not in details
    assert "&lt;script&gt;" in details
    assert "2026-10-19T18:28:39Z | Loki ref" in details


def test_missing_sources_are_explained_in_plain_language():
    text = missing_summary(["kubernetes_events", "mimir", "CPU historique des conteneurs absent sur cette fenêtre"])
    assert "événements Kubernetes incomplets" in text
    assert "métriques Mimir indisponibles" in text
    assert "CPU historique du conteneur non collecté" in text
    assert "kubernetes_events" not in text
