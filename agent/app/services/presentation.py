"""French display of UTC evidence without changing the underlying instants."""

from datetime import datetime
from html import escape
import re
from zoneinfo import ZoneInfo

MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
          "septembre", "octobre", "novembre", "décembre")
EN_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December")
DEFAULT_ZONE = "Europe/Zurich"


def local_time(value: str | datetime, zone: str = DEFAULT_ZONE) -> datetime:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("Un horodatage avec fuseau est requis")
    return instant.astimezone(ZoneInfo(zone))


def date_label(moment: datetime, language: str = "fr") -> str:
    months = EN_MONTHS if language == "en" else MONTHS
    return f"{moment.day} {months[moment.month - 1]} {moment.year}"


def offset_label(moment: datetime) -> str:
    seconds = int(moment.utcoffset().total_seconds())
    sign = "+" if seconds >= 0 else "−"
    hours, remainder = divmod(abs(seconds), 3600)
    minutes = remainder // 60
    return f"UTC{sign}{hours}" + (f":{minutes:02d}" if minutes else "")


def zone_label(zone: str, language: str = "fr") -> str:
    if language == "en":
        return "Swiss time" if zone == "Europe/Zurich" else "local time"
    return "Heure suisse" if zone == "Europe/Zurich" else "Heure locale"


def full_time(value: str | datetime, zone: str = DEFAULT_ZONE, language: str = "fr") -> str:
    moment = local_time(value, zone)
    if language == "en":
        return (f"{date_label(moment, 'en')} at {moment.hour:02d}:{moment.minute:02d}:"
                f"{moment.second:02d} ({zone_label(zone, 'en')} — {zone}, {offset_label(moment)})")
    return (f"{date_label(moment)} à {moment.hour:02d} h {moment.minute:02d} min "
            f"{moment.second:02d} s ({zone_label(zone).lower()} — {zone}, {offset_label(moment)})")


def short_time(value: str | datetime, zone: str = DEFAULT_ZONE) -> str:
    return local_time(value, zone).strftime("%H:%M:%S")


def day_heading(moment: datetime, zone: str, *, mixed_offset: bool = False,
                language: str = "fr") -> list[str]:
    offset = ("offset indiqué pour chaque événement" if language == "fr"
              else "offset shown for each event") if mixed_offset else offset_label(moment)
    label = zone_label(zone, language)
    if zone != DEFAULT_ZONE:
        label = f"{label} — {zone}"
    return [f"**{date_label(moment, language)} · {label} ({offset})**", ""]


def technical_details(lines: list[str], *, title: str = "Détails techniques / Preuves") -> str:
    """Emit a safe code block marker; the WebUI adapter turns it into <details>."""
    cleaned = []
    for line in lines:
        line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
        line = line.replace("`", "")
        cleaned.append(escape(line, quote=False))
    safe = "\n".join(cleaned)
    return ("\n\n```text\nKUBEAI_TECHNICAL_DETAILS|" + title + "\n" +
            safe + "\n```")


def missing_summary(items: list[str], *, limit: int = 3) -> str:
    labels = {"kubernetes_events": "événements Kubernetes incomplets",
              "kubernetes_pods": "inventaire Kubernetes incomplet",
              "kubernetes": "données Kubernetes indisponibles",
              "mimir": "métriques Mimir indisponibles",
              "loki": "logs Loki indisponibles",
              "taux d'erreurs HTTP 5xx absent sur cette fenêtre": "taux d'erreurs HTTP non mesuré",
              "latence HTTP p95 absente sur cette fenêtre": "latence HTTP p95 non mesurée"}
    selected = []
    for item in items:
        if "aucun événement pertinent conservé" in item:
            label = "événements Kubernetes historiques non conservés"
        elif "CPU historique des conteneurs absent" in item:
            label = "CPU historique du conteneur non collecté"
        elif "mémoire historique des conteneurs absente" in item:
            label = "mémoire historique du conteneur non collectée"
        else:
            label = labels.get(item, item)
        if label not in selected:
            selected.append(label)
    suffix = f" ; {len(selected) - limit} autre(s) limite(s) dans les preuves" if len(selected) > limit else ""
    return "; ".join(selected[:limit]) + suffix


def readable_observation(item: dict) -> str:
    """Short source-backed proof for the main answer; exact line stays in details."""
    observation = item["observation"]
    if item["source"] == "Loki":
        marker = re.search(r"\bphase\s*=\s*(normal|anomaly_start|recovered|complete)\b", observation, re.I)
        if marker:
            return f"Marqueur `{marker.group(1).lower()}` enregistré dans Loki."
        observation = re.sub(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\s*", "", observation)
    if item["source"] == "Mimir":
        observation = re.sub(r"(?<=\d)\.(?=\d)", ",", observation)
        observation = observation.replace("cœur(s)", "cœur")
    return observation[:200]
