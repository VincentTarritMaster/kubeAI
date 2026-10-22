"""A day search must not sample past a short incident or mix phase cycles."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.clients.loki_client import HistoricalLogs
from app.clients.prometheus_client import HistoricalMetrics, HistoricalPoint, PrometheusClient
from app.config import Settings
from app.models import SourceStatus
from app.services.correlation import assess, render as render_correlation
from app.services.incident_timeline import (
    _historical_log_signal, _refined_day_window, application_cpu_episode, build, collect, render, scan_date,
)


DAY = datetime(2026, 10, 19, tzinfo=timezone.utc)


def at(minute, second=0):
    return DAY + timedelta(hours=18, minutes=minute, seconds=second)


PHASES = [(at(22, 45), "normal"), (at(24, 15), "anomaly_start"),
          (at(27, 9), "normal"), (at(28, 39), "anomaly_start"),
          (at(30, 9), "recovered"), (at(31, 9), "complete")]


def log(moment, message):
    return {"namespace": "operations", "pod": "sample-worker", "container": "worker",
            "timestamp_ns": str(int(moment.timestamp() * 10**9)), "message": message}


async def investigate_day(*, phases=PHASES, app_cpu=True):
    calls = []
    labels = {"namespace": "operations", "pod": "sample-worker"}
    entries = [log(moment, f"WORKER_INCIDENT phase={phase}") for moment, phase in phases]

    def mimir(request):
        calls.append(request)
        start, end = (float(request.url.params[key]) for key in ("start", "end"))
        step = int(request.url.params["step"])
        assert 0 < end - start <= 6 * 3600
        assert 'namespace="operations",pod="sample-worker"' in request.url.params["query"]
        query = request.url.params["query"]
        samples = []
        moment = start
        while moment <= end:
            # The first execution ended before its process was scraped. Only
            # the second run has a real process series, for under five minutes.
            if "process_cpu_seconds_total" in query and app_cpu and at(27, 9).timestamp() <= moment <= at(31, 9).timestamp():
                value = 0.08 if at(28, 39).timestamp() <= moment < at(30, 9).timestamp() else 0.002
                samples.append([moment, str(value)])
            elif "kube_pod_container_" in query and at(20).timestamp() <= moment <= at(33).timestamp():
                value = "0" if "restarts_total" in query else "0.1" if 'resource="cpu"' in query else "67108864"
                samples.append([moment, value])
            moment += step
        result = [{"metric": labels, "values": samples}] if samples else []
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": result}})

    class Logs:
        async def logs_between(self, start, end, namespace, pod):
            return HistoricalLogs([item for item in entries if start <= int(item["timestamp_ns"]) <= end])

    inventory = SimpleNamespace(events=[], pods=[], warnings=[], observed_at=DAY,
                                sources={name: SourceStatus(status="ok") for name in ("events", "pods")})

    async def get_inventory():
        return inventory

    async with httpx.AsyncClient(base_url="http://mimir/prometheus/", transport=httpx.MockTransport(mimir)) as http:
        service = SimpleNamespace(
            cluster_status=SimpleNamespace(inventory=get_inventory,
                prometheus=PrometheusClient(http, Settings(ca_file="", token_file=""))),
            loki=Logs(), sensitive_values=lambda: [])
        parts = {"date": DAY, "namespace": "operations", "pod": "sample-worker"}
        window, error, scanned = await scan_date(service, parts, inventory)
        assert error is None
        if scanned:
            report, _, metrics, logs = scanned
        else:
            report, _, metrics, logs = await collect(service, window, include_correlation=True)
        return window, report, metrics, logs, calls


@pytest.mark.parametrize("message", ["WORKER_INCIDENT phase=normal", "WORKER_INCIDENT phase=recovered",
                                     "WORKER_INCIDENT phase=complete", "incident observer is ready",
                                     "phase=healthy", "no incident observed"])
def test_healthy_phase_or_incident_name_is_not_a_failure_signal(message):
    assert _historical_log_signal(message) == 0


@pytest.mark.parametrize("message", ["WORKER_INCIDENT phase=anomaly_start", "app : connection refused",
                                     "worker failed", "application timeout", "OOMKilled"])
def test_actual_failure_is_a_search_signal(message):
    assert _historical_log_signal(message) > 0


def test_day_search_refines_around_real_anomaly_and_keeps_short_cpu_series():
    window, report, metrics, _, calls = asyncio.run(investigate_day())
    assert at(20, 45) == window["start"]
    assert at(33, 9) == window["end"]
    assert "signal daté à 2026-10-19T18:24:15Z" in window["search_note"]
    assert "signal daté à 2026-10-19T18:22:45Z" not in window["search_note"]
    assert len(calls) == 20  # Twelve bounded scans + eight focused series.
    process_call = next(request for request in calls if "process_cpu_seconds_total" in request.url.params["query"])
    assert int(process_call.url.params["step"]) == 30
    assert len([point for point in metrics.points if point.metric == "app_cpu_cores"]) >= 6
    assert len(report["timeline"]) <= 100
    for _, phase in PHASES:
        assert any(f"phase={phase}" in item["observation"] for item in report["timeline"])


def test_restarted_run_uses_its_own_measured_baseline_peak_and_recovery():
    _, report, metrics, _, _ = asyncio.run(investigate_day())
    result = assess(report, metrics)
    item = next(hypothesis for hypothesis in result["hypotheses"] if hypothesis["id"] == "demo_app_cpu")
    proofs = item["supporting_observations"]
    assert any(proof["timestamp"] == "2026-10-19T18:28:39Z" for proof in proofs)
    assert not any(proof["timestamp"] == "2026-10-19T18:24:15Z" for proof in proofs)
    assert any("app_cpu_cores" in proof["reference"] and "0.0800" in proof["observation"] for proof in proofs)
    text = render(report, "Analyse déterministe.")
    assert "Cycle retenu à 20:28:39" in text
    assert "2026-10-19T18:28:39Z" in text.split("<details>", 1)[1]
    correlation = render_correlation(result, "Analyse déterministe.")
    visible = correlation.split("<details>", 1)[0]
    assert "**19 octobre 2026 · Heure suisse (UTC+2)**" in visible
    assert "Problème observé :** anomalie signalée dans les logs Loki à 20:28:39" in visible
    assert "2026-10-19T18:28:39Z" not in visible
    assert "2026-10-19T18:28:39Z" in correlation.split("<details>", 1)[1]
    assert "throttling" in visible and "lien causal reste à vérifier" in visible
    assert "0,0020 → 0,0800 cœur" in text
    assert any("app_cpu_cores" in proof["reference"] and "0.0020" in proof["observation"]
               and proof["timestamp"] > "2026-10-19T18:30:09Z" for proof in proofs)
    assert "causalité non prouvée" in item["confidence"]


def test_healthy_phases_report_the_searched_day_without_fabricating_anomaly():
    phases = [(at(22, 45), "normal"), (at(30, 9), "recovered"), (at(31, 9), "complete")]
    window, report, _, _, calls = asyncio.run(investigate_day(phases=phases, app_cpu=False))
    assert window["start"] == DAY and window["end"] == DAY + timedelta(days=1)
    assert len(calls) == 12  # Two bounded Mimir queries, one signal query and Loki per segment.
    assert any("Aucune anomalie horodatée" in warning for warning in report["warnings"])


def test_absent_process_samples_remain_an_explicit_limitation():
    _, report, metrics, _, _ = asyncio.run(investigate_day(app_cpu=False))
    result = assess(report, metrics)
    assert not any(hypothesis["id"] == "demo_app_cpu" for hypothesis in result["hypotheses"])
    assert any("série process_cpu_seconds_total absente" in limitation for limitation in result["correlation_limits"])


def test_retained_mimir_cpu_finds_incident_when_loki_has_expired_its_logs():
    window, report, metrics, logs, calls = asyncio.run(investigate_day(phases=[]))
    assert window["start"] == at(24)
    assert window["end"] == at(34)
    assert logs.entries == []
    assert any(point.metric == "app_cpu_cores" and point.value == 0.08 for point in metrics.points)
    assert any(entry["source"] == "Mimir" and "app_cpu_cores" in entry["reference"]
               for entry in report["timeline"])
    assert any("aucun log pertinent conservé" in missing for missing in report["missing"])
    assert report["data_status"] == "partial"
    signal_queries = [request for request in calls if "process_cpu_seconds_total" in request.url.params["query"]]
    assert signal_queries and all(int(request.url.params["step"]) == 30 for request in signal_queries)


def test_baseline_from_a_previous_run_cannot_support_a_later_peak():
    entries = [{"timestamp": moment.isoformat(), "observation": f"WORKER phase={phase}"}
               for moment, phase in PHASES]
    points = [HistoricalPoint(at(23).timestamp(), "app_cpu_cores", 0.002, {}),
              HistoricalPoint(at(29).timestamp(), "app_cpu_cores", 0.08, {})]
    episode = application_cpu_episode(entries, points)
    assert episode["start_time"] == at(28, 39).timestamp()
    assert episode["baseline"] is None
    assert episode["measured_rise"] is False


def test_dense_timeline_keeps_late_phases_cpu_peak_and_each_series_endpoints():
    start, end = at(0), at(30)
    labels = {"namespace": "operations", "pod": "sample-worker"}
    kinds = {"cpu_cores": 0.01, "app_cpu_cores": 0.002,
             "memory_working_set_bytes": 16 * 1024 * 1024, "restart_count": 0,
             "cpu_limit_cores": 0.1, "memory_limit_bytes": 64 * 1024 * 1024}
    points = []
    peak_at = at(28, 30)
    for kind, baseline in kinds.items():
        for offset in range(0, 1801, 30):
            moment = start + timedelta(seconds=offset)
            value = 0.08 if kind == "app_cpu_cores" and moment == peak_at else baseline
            points.append(HistoricalPoint(moment.timestamp(), kind, value, labels))
    phases = [(at(27), "normal"), (at(28), "anomaly_start"),
              (at(29), "recovered"), (at(29, 45), "complete")]
    entries = [log(moment, f"WORKER_INCIDENT phase={phase}") for moment, phase in phases]
    inventory = SimpleNamespace(events=[], pods=[], warnings=[], observed_at=end,
                                sources={name: SourceStatus(status="ok") for name in ("events", "pods")})
    window = {"start": start, "end": end, "timezone": "UTC", "namespace": "operations", "pod": "sample-worker"}
    assert len(points) + len(entries) > 100
    report = build(window, inventory, HistoricalMetrics(points, []), HistoricalLogs(entries), {})
    timeline = report["timeline"]
    assert len(timeline) <= 100
    assert report["data_status"] == "partial"
    assert [entry["timestamp"] for entry in timeline] == sorted(entry["timestamp"] for entry in timeline)
    for _, phase in phases:
        assert any(f"phase={phase}" in entry["observation"] for entry in timeline)
    for kind in kinds:
        selected = [entry for entry in timeline if f"query_range {kind} " in entry["reference"]]
        assert selected[0]["timestamp"] == "2026-10-19T18:00:00Z"
        assert selected[-1]["timestamp"] == "2026-10-19T18:30:00Z"
    assert any("app_cpu_cores" in entry["reference"] and entry["timestamp"] == "2026-10-19T18:28:30Z"
               and "0.0800" in entry["observation"] for entry in timeline)
    # assess references these exact observed neighbors when justifying the
    # rise and the recovery; retaining extrema alone cannot preserve them.
    for moment in ("2026-10-19T18:27:30Z", "2026-10-19T18:29:00Z"):
        assert any("app_cpu_cores" in entry["reference"] and entry["timestamp"] == moment for entry in timeline)


@pytest.mark.parametrize("minute", [1, 1439])
def test_refined_window_stays_inside_the_requested_utc_day(minute):
    moment = DAY + timedelta(minutes=minute)
    start, end, anchor = _refined_day_window(DAY, [(2, moment)], [])
    assert DAY <= start < anchor < end <= DAY + timedelta(days=1)
    assert timedelta(minutes=10) == end - start
