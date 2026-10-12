#!/usr/bin/env python3
"""Lokaler Modellradar für explizit erlaubte Hugging-Face-Herausgeber.

Benötigt ausschließlich die Python-Standardbibliothek. Das Programm lädt keine
Modelle herunter und übergibt keinerlei Rechte oder Werkzeuge an ein LLM.
"""

from __future__ import annotations

import argparse
import email.utils
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import radar_platform

VERSION = "2.6.0"
CONFIG_SCHEMA_VERSION = 2
STATE_SCHEMA_VERSION = 2
FILTER_MODES = {"conservative", "strict_text"}
USER_AGENT = f"qwen35-model-radar/{VERSION} (public metadata only)"
QUANT_RE = re.compile(
    r"(?<![A-Z0-9])((?:IQ|Q|BF|F)\d(?:_[A-Z0-9]+)*(?:-[A-Z0-9]+)?)(?![A-Z0-9])",
    re.IGNORECASE,
)
PARAM_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*B(?:\b|[_-])", re.IGNORECASE)


class RadarError(RuntimeError):
    """Erwartbarer Fehler mit einer für Menschen geeigneten Meldung."""


@dataclass(frozen=True)
class Candidate:
    repo_id: str
    publisher: str
    family: str
    sha: str | None
    last_modified: str | None
    parameters_b: float | None
    parameter_source: str
    pipeline_tag: str | None
    repo_kind: str
    quantizations: tuple[str, ...]
    reason: str

    @property
    def url(self) -> str:
        return "https://huggingface.co/" + urllib.parse.quote(self.repo_id, safe="/")


Change = tuple[str, Candidate, str]


@dataclass(frozen=True)
class CheckResult:
    """Strukturiertes Ergebnis einer Prüfung, noch ohne externe Nebenwirkungen."""

    due: bool
    message: str
    report: str = ""
    new_state: dict[str, Any] | None = None
    changes: tuple[Change, ...] = ()
    candidates: tuple[Candidate, ...] = ()
    rejected: tuple[str, ...] = ()
    pruned_repositories: tuple[str, ...] = ()
    checked_at: str | None = None
    first_run: bool = False
    notify_required: bool = False


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().replace(microsecond=0).isoformat()


def default_config_path() -> Path:
    beside_script = Path(__file__).resolve().with_name("config.json")
    if beside_script.is_file():
        return beside_script
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "qwen35" / "model-radar.json"


def load_json(path: Path, label: str) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise RadarError(f"{label} nicht gefunden: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RadarError(f"{label} kann nicht gelesen werden ({path}): {exc}") from exc


def migrate_config(raw: Any) -> dict[str, Any]:
    """Migriert ältere Konfigurationen ohne ihre benutzerdefinierten Werte zu verlieren."""
    if not isinstance(raw, dict):
        raise RadarError("Die Konfiguration muss ein JSON-Objekt sein.")
    config = dict(raw)
    version = config.get("schema_version", 0)
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise RadarError("Ungültige schema_version in der Konfiguration.")
    if version > CONFIG_SCHEMA_VERSION:
        raise RadarError(
            f"Konfigurationsschema {version} ist neuer als die unterstützte Version "
            f"{CONFIG_SCHEMA_VERSION}."
        )
    if version == 0:
        config.setdefault("filter_mode", "conservative")
        config["schema_version"] = 1
        version = 1
    if version == 1:
        config.setdefault("absent_retention_days", 365)
        config["schema_version"] = 2
    return config


def validate_config(raw: Any) -> dict[str, Any]:
    raw = migrate_config(raw)
    required = {
        "api_url", "publishers", "families", "parameter_min_b",
        "parameter_max_b", "state_file", "report_file",
    }
    missing = sorted(required - raw.keys())
    if missing:
        raise RadarError("Fehlende Konfigurationsfelder: " + ", ".join(missing))
    parsed = urllib.parse.urlparse(str(raw["api_url"]))
    if parsed.scheme != "https" or parsed.hostname != "huggingface.co" or parsed.path != "/api/models":
        raise RadarError("api_url muss exakt auf https://huggingface.co/api/models zeigen.")
    if not isinstance(raw["families"], dict) or not raw["families"]:
        raise RadarError("families muss ein nicht leeres Objekt sein.")
    for family, terms in raw["families"].items():
        if (
            not isinstance(family, str)
            or not family.strip()
            or not isinstance(terms, list)
            or not terms
            or any(not isinstance(term, str) or not term.strip() for term in terms)
        ):
            raise RadarError(f"Ungültige Suchbegriffe für Modellfamilie {family!r}.")
    if not isinstance(raw["publishers"], dict) or not raw["publishers"]:
        raise RadarError("publishers muss ein nicht leeres Objekt sein.")
    known_families = set(raw["families"])
    for publisher, families in raw["publishers"].items():
        if (
            not isinstance(publisher, str)
            or not publisher.strip()
            or not isinstance(families, list)
            or not families
            or any(not isinstance(family, str) for family in families)
            or not set(families) <= known_families
        ):
            raise RadarError(f"Ungültige Familienfreigabe für Herausgeber {publisher!r}.")
    try:
        low, high = float(raw["parameter_min_b"]), float(raw["parameter_max_b"])
    except (TypeError, ValueError) as exc:
        raise RadarError("Der Parameterbereich muss aus Zahlen bestehen.") from exc
    if not math.isfinite(low) or not math.isfinite(high) or low <= 0 or high < low:
        raise RadarError("Der Parameterbereich ist ungültig.")
    for key in ("check_interval_days", "request_timeout_seconds", "request_retries", "page_size", "max_pages_per_query"):
        value = raw.get(key, 1)
        if isinstance(value, bool):
            raise RadarError(f"{key} muss eine ganze Zahl ab 1 sein.")
        try:
            integer = int(value)
        except (TypeError, ValueError) as exc:
            raise RadarError(f"{key} muss eine ganze Zahl ab 1 sein.") from exc
        if integer < 1 or isinstance(value, float) and not value.is_integer():
            raise RadarError(f"{key} muss mindestens 1 sein.")
    retention = raw.get("absent_retention_days", 365)
    if isinstance(retention, bool):
        raise RadarError("absent_retention_days muss eine ganze Zahl ab 0 sein.")
    try:
        retention_days = int(retention)
    except (TypeError, ValueError) as exc:
        raise RadarError("absent_retention_days muss eine ganze Zahl ab 0 sein.") from exc
    if retention_days < 0 or isinstance(retention, float) and not retention.is_integer():
        raise RadarError("absent_retention_days muss eine ganze Zahl ab 0 sein.")
    try:
        backoff = float(raw.get("retry_backoff_seconds", 2))
    except (TypeError, ValueError) as exc:
        raise RadarError("retry_backoff_seconds muss eine nichtnegative Zahl sein.") from exc
    if not math.isfinite(backoff) or backoff < 0:
        raise RadarError("retry_backoff_seconds muss eine nichtnegative Zahl sein.")
    data_paths: dict[str, Path] = {}
    for key, label in (("state_file", "state_file"), ("report_file", "report_file")):
        value = raw[key]
        if not isinstance(value, str) or not value.strip():
            raise RadarError(f"{label} muss ein nicht leerer Dateipfad sein.")
        data_paths[key] = Path(value).expanduser().resolve(strict=False)
    if data_paths["state_file"] == data_paths["report_file"]:
        raise RadarError("state_file und report_file müssen unterschiedliche Dateien sein.")
    if raw.get("filter_mode") not in FILTER_MODES:
        raise RadarError("filter_mode muss 'conservative' oder 'strict_text' sein.")
    preferred = raw.get("preferred_pipeline_tags", [])
    if (
        not isinstance(preferred, list)
        or any(not isinstance(tag, str) or not tag.strip() for tag in preferred)
        or raw["filter_mode"] == "strict_text" and not preferred
    ):
        raise RadarError(
            "preferred_pipeline_tags muss eine Liste sein und darf im strikten Modus nicht leer sein."
        )
    return raw


def load_config(path: Path, *, persist_migration: bool = False) -> dict[str, Any]:
    """Lädt und prüft die Konfiguration; schreibt alte Schemata auf Wunsch zurück."""
    raw = load_json(path, "Konfiguration")
    config = validate_config(raw)
    if persist_migration and raw.get("schema_version", 0) != CONFIG_SCHEMA_VERSION:
        persist_migrated_file(path, config, raw.get("schema_version", 0))
    return config


def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    atomic_write_bytes(path, text.encode("utf-8"), mode)


def atomic_write_bytes(path: Path, content: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def persist_migrated_file(path: Path, migrated: dict[str, Any], old_version: int) -> Path:
    """Sichert die unveränderten Bytes und ersetzt eine alte JSON-Datei atomar.

    Bleibt der Ersatz nach einem Fehler teilweise bestehen, wird die Originaldatei
    aus denselben Bytes wiederhergestellt. Die Sicherung bleibt auch nach Erfolg.
    """
    if path.is_symlink():
        raise RadarError(f"Migration eines symbolischen Links ist nicht erlaubt: {path}")
    original = path.read_bytes()
    backup_base = path.with_name(f"{path.name}.schema-v{old_version}.bak")
    backup = backup_base
    index = 1
    while os.path.lexists(backup):
        backup = path.with_name(f"{backup_base.name}.{index}")
        index += 1
    atomic_write_bytes(backup, original)
    try:
        atomic_write(path, json.dumps(migrated, indent=2, ensure_ascii=False) + "\n")
    except BaseException:
        try:
            if path.read_bytes() != original:
                atomic_write_bytes(path, original)
        except OSError as rollback_error:
            raise RadarError(
                f"Migration fehlgeschlagen; Wiederherstellung von {path} fehlgeschlagen. "
                f"Original liegt in {backup}: {rollback_error}"
            ) from rollback_error
        raise
    return backup


def parse_link_next(header: str | None, api_url: str) -> str | None:
    if not header:
        return None
    for part in header.split(","):
        match = re.match(r'\s*<([^>]+)>\s*;\s*rel="?next"?', part)
        if not match:
            continue
        candidate = urllib.parse.urljoin(api_url, match.group(1))
        parsed = urllib.parse.urlparse(candidate)
        base = urllib.parse.urlparse(api_url)
        if (
            parsed.scheme == "https"
            and parsed.netloc == base.netloc
            and parsed.path == base.path
            and parsed.username is None
            and parsed.password is None
        ):
            return candidate
        raise RadarError("Unsicheres Pagination-Ziel von der API verworfen.")
    return None


def retry_delay(error: urllib.error.HTTPError, attempt: int, base: float) -> float:
    retry_after = error.headers.get("Retry-After")
    if retry_after:
        try:
            return min(float(retry_after), 120.0)
        except ValueError:
            try:
                target = email.utils.parsedate_to_datetime(retry_after)
                return max(0.0, min((target - utc_now()).total_seconds(), 120.0))
            except (TypeError, ValueError):
                pass
    return min(base * (2**attempt), 30.0)


def fetch_json_page(url: str, config: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    retries = int(config.get("request_retries", 3))
    for attempt in range(retries):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=float(config.get("request_timeout_seconds", 20))) as response:
                payload = json.load(response)
                if not isinstance(payload, list):
                    raise RadarError("Die Hugging-Face-API lieferte kein JSON-Array.")
                return payload, parse_link_next(response.headers.get("Link"), config["api_url"])
        except urllib.error.HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code <= 599
            if not retryable or attempt + 1 == retries:
                detail = "Rate Limit" if exc.code == 429 else f"HTTP {exc.code}"
                raise RadarError(f"Hugging-Face-API nicht erreichbar: {detail}.") from exc
            time.sleep(retry_delay(exc, attempt, float(config.get("retry_backoff_seconds", 2))))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt + 1 == retries:
                raise RadarError(f"Hugging-Face-API nicht erreichbar: {exc}") from exc
            time.sleep(float(config.get("retry_backoff_seconds", 2)) * (2**attempt))
    raise AssertionError("Unerreichbarer Code")


def fetch_models(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Fragt nur explizit konfigurierte Herausgeber/Familien ab; keine Tokens."""
    found: dict[str, dict[str, Any]] = {}
    for publisher, families in config["publishers"].items():
        for family in families:
            query = urllib.parse.urlencode({
                "author": publisher,
                "search": family,
                "sort": "lastModified",
                "direction": "-1",
                "limit": int(config.get("page_size", 100)),
                "full": "true",
                "config": "true",
            })
            next_url: str | None = f"{config['api_url']}?{query}"
            max_pages = int(config.get("max_pages_per_query", 10))
            for _ in range(max_pages):
                if next_url is None:
                    break
                page, next_url = fetch_json_page(next_url, config)
                for model in page:
                    repo_id = model.get("id") or model.get("modelId")
                    if isinstance(repo_id, str):
                        found[repo_id] = model
            if next_url is not None:
                raise RadarError(
                    f"API-Abfrage für {publisher}/{family} nach {max_pages} Seiten abgebrochen; "
                    "max_pages_per_query erhöhen, damit kein unvollständiger Zustand gespeichert wird."
                )
    return list(found.values())


def family_for(name: str, allowed: Iterable[str], config: dict[str, Any]) -> str | None:
    lower = name.lower()
    for family in allowed:
        terms = config["families"].get(family, [])
        if any(str(term).lower() in lower for term in terms):
            return family
    return None


def numeric_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value) and value > 0:
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([BbMm]?)", cleaned)
        if match:
            number = float(match.group(1))
            suffix = match.group(2).lower()
            result = number * (1e9 if suffix == "b" else 1e6 if suffix == "m" else 1)
            return result if math.isfinite(result) else None
    return None


def parameter_count(model: dict[str, Any]) -> tuple[float | None, str]:
    for section_name in ("safetensors", "gguf"):
        section = model.get(section_name)
        if isinstance(section, dict):
            for key in ("total", "parameters", "parameter_count"):
                value = numeric_value(section.get(key))
                if value:
                    return value / 1e9, f"API-Feld {section_name}.{key}"
    model_config = model.get("config")
    if isinstance(model_config, dict):
        for key in ("num_parameters", "parameter_count", "n_params"):
            value = numeric_value(model_config.get(key))
            if value:
                return value / 1e9, f"API-Feld config.{key}"
    for tag in model.get("tags") or []:
        match = re.search(r"(?:params?|parameters?)[:=_-](\d+(?:\.\d+)?)\s*([BM])\b", str(tag), re.I)
        if match:
            value = float(match.group(1)) * (1.0 if match.group(2).upper() == "B" else 0.001)
            return value, f"Tag {tag}"
    name_match = PARAM_RE.search(str(model.get("id") or model.get("modelId") or ""))
    if name_match:
        return float(name_match.group(1)), "Repository-Name (Heuristik)"
    return None, "nicht erkennbar"


def quantizations(model: dict[str, Any]) -> tuple[str, ...]:
    values: set[str] = set()
    for sibling in model.get("siblings") or []:
        filename = sibling.get("rfilename", "") if isinstance(sibling, dict) else str(sibling)
        if filename.lower().endswith(".gguf"):
            values.update(match.upper() for match in QUANT_RE.findall(Path(filename).name.upper()))
    return tuple(sorted(values))


def select_candidates(models: Iterable[dict[str, Any]], config: dict[str, Any]) -> tuple[list[Candidate], list[str]]:
    selected: list[Candidate] = []
    rejected: list[str] = []
    excluded = tuple(str(term).lower() for term in config.get("excluded_name_terms", []))
    preferred = {str(tag).lower() for tag in config.get("preferred_pipeline_tags", [])}
    strict_text = config.get("filter_mode", "conservative") == "strict_text"
    low, high = float(config["parameter_min_b"]), float(config["parameter_max_b"])
    for model in models:
        repo_id = str(model.get("id") or model.get("modelId") or "")
        if "/" not in repo_id:
            rejected.append(f"{repo_id or '(ohne ID)'}: ungültige Repository-ID")
            continue
        publisher, name = repo_id.split("/", 1)
        if publisher not in config["publishers"]:
            rejected.append(f"{repo_id}: Herausgeber nicht freigegeben")
            continue
        family = family_for(name, config["publishers"][publisher], config)
        if family is None:
            rejected.append(f"{repo_id}: keine freigegebene Modellfamilie")
            continue
        if any(term in name.lower() for term in excluded):
            rejected.append(f"{repo_id}: klar nicht-textuelle/spezialisierte Variante")
            continue
        size_b, size_source = parameter_count(model)
        if size_b is None:
            rejected.append(f"{repo_id}: Parametergröße nicht erkennbar")
            continue
        if not low <= size_b <= high:
            rejected.append(f"{repo_id}: {size_b:g}B außerhalb {low:g}–{high:g}B")
            continue
        tags = {str(tag).lower() for tag in model.get("tags") or []}
        pipeline = model.get("pipeline_tag") or model.get("pipelineTag")
        normalized_pipeline = str(pipeline).lower() if pipeline else None
        is_gguf = "gguf" in tags or any(
            str(item.get("rfilename", "")).lower().endswith(".gguf")
            for item in model.get("siblings") or [] if isinstance(item, dict)
        )
        kind = "GGUF-Repository" if is_gguf else "Basismodell-/Transformers-Repository"
        if strict_text and normalized_pipeline not in preferred:
            rejected.append(
                f"{repo_id}: Pipeline {pipeline or 'nicht angegeben'} im strikten Textmodell-Modus verworfen"
            )
            continue
        if normalized_pipeline in preferred:
            preference = "bevorzugte Text-Pipeline"
        elif pipeline:
            preference = f"Pipeline {pipeline} ist nicht rein textuell"
        elif "instruct" in name.lower() or "-it" in name.lower():
            preference = "Instruct-Signal im Namen; Pipeline nicht angegeben"
        else:
            preference = "möglicherweise Basismodell; Text-Eignung anhand Metadaten nicht eindeutig"
        reason = (
            f"exakt freigegebener Herausgeber; Familie {family}; {size_b:g}B im Bereich; "
            f"{kind}; {preference}"
        )
        selected.append(Candidate(
            repo_id=repo_id, publisher=publisher, family=family,
            sha=model.get("sha"), last_modified=model.get("lastModified"),
            parameters_b=size_b, parameter_source=size_source,
            pipeline_tag=str(pipeline) if pipeline else None,
            repo_kind=kind, quantizations=quantizations(model), reason=reason,
        ))
    selected.sort(key=lambda candidate: (candidate.publisher.lower(), candidate.repo_id.lower()))
    return selected, rejected


def migrate_state(raw: Any, path: Path | None = None) -> dict[str, Any]:
    """Hebt v1-Zustände auf v2; ein reiner Aufruf verändert keine Dateien."""
    location = f": {path}" if path else ""
    if not isinstance(raw, dict) or not isinstance(raw.get("repositories"), dict):
        raise RadarError(f"Ungültiges Zustandsformat{location}")
    version = raw.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise RadarError(f"Ungültiges Zustandsformat{location}")
    if version > STATE_SCHEMA_VERSION:
        raise RadarError(
            f"Zustandsschema {version} ist neuer als die unterstützte Version {STATE_SCHEMA_VERSION}{location}"
        )
    if version < 1:
        raise RadarError(f"Ungültiges Zustandsformat{location}")
    state = dict(raw)
    repositories = {}
    for repo_id, value in raw["repositories"].items():
        if not isinstance(repo_id, str) or not isinstance(value, dict):
            raise RadarError(f"Ungültiges Zustandsformat{location}")
        record = dict(value)
        if version == 1:
            record.setdefault("present", True)
            record.setdefault("missing_since", None)
        repositories[repo_id] = record
    state["repositories"] = repositories
    state["schema_version"] = STATE_SCHEMA_VERSION
    return state


def load_state(path: Path, *, persist_migration: bool = False) -> tuple[dict[str, Any], bool]:
    if not path.exists():
        return {"schema_version": STATE_SCHEMA_VERSION, "repositories": {}}, True
    raw = load_json(path, "Zustandsdatei")
    state = migrate_state(raw, path)
    if persist_migration and raw["schema_version"] != STATE_SCHEMA_VERSION:
        persist_migrated_file(path, state, raw["schema_version"])
    return state, False


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc)


def next_check_at(state: dict[str, Any], interval_days: int) -> datetime | None:
    """Liefert den nächsten echten API-Fälligkeitszeitpunkt in UTC."""
    last_check = parse_timestamp(state.get("last_successful_check"))
    if last_check is None:
        return None
    return last_check + timedelta(days=int(interval_days))


def check_is_due(state: dict[str, Any], interval_days: int, now: datetime | None = None) -> bool:
    """Prüft, ob seit dem letzten Erfolg mindestens das Intervall vergangen ist."""
    due_at = next_check_at(state, interval_days)
    if due_at is None:
        return True
    current = now or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc) >= due_at


def candidate_snapshot(candidate: Candidate) -> dict[str, Any]:
    return {
        "publisher": candidate.publisher,
        "family": candidate.family,
        "parameters_b": candidate.parameters_b,
        "parameter_source": candidate.parameter_source,
        "pipeline_tag": candidate.pipeline_tag,
        "repo_kind": candidate.repo_kind,
        "quantizations": list(candidate.quantizations),
        "reason": candidate.reason,
    }


def candidate_from_state(repo_id: str, record: dict[str, Any]) -> Candidate:
    """Rekonstruiert Berichtsdaten; migrierte v1-Zustände dürfen Details vermissen."""
    snapshot = record.get("candidate")
    if not isinstance(snapshot, dict):
        snapshot = {}
    publisher = str(snapshot.get("publisher") or repo_id.partition("/")[0] or "unbekannt")
    parameters = numeric_value(snapshot.get("parameters_b"))
    return Candidate(
        repo_id=repo_id,
        publisher=publisher,
        family=str(snapshot.get("family") or "nicht im Altzustand gespeichert"),
        sha=record.get("sha"),
        last_modified=record.get("last_modified"),
        parameters_b=parameters,
        parameter_source=str(snapshot.get("parameter_source") or "nicht im Altzustand gespeichert"),
        pipeline_tag=str(snapshot["pipeline_tag"]) if snapshot.get("pipeline_tag") else None,
        repo_kind=str(snapshot.get("repo_kind") or "nicht im Altzustand gespeichert"),
        quantizations=tuple(str(value) for value in snapshot.get("quantizations", [])),
        reason=str(snapshot.get("reason") or "aus lokalem Zustand rekonstruiert"),
    )


def compare(candidates: Iterable[Candidate], state: dict[str, Any]) -> list[Change]:
    changes: list[Change] = []
    known = state["repositories"]
    current = {candidate.repo_id: candidate for candidate in candidates}
    for candidate in current.values():
        previous = known.get(candidate.repo_id)
        if previous is None:
            changes.append(("NEU", candidate, "Repository-ID war im lokalen Zustand nicht bekannt"))
            continue
        if not previous.get("present", True):
            since = previous.get("missing_since") or "unbekannt"
            changes.append(("WIEDER ERSCHIENEN", candidate, f"seit {since} als abwesend markiert"))
            continue
        reasons = []
        if candidate.sha and candidate.sha != previous.get("sha"):
            reasons.append("SHA geändert")
        if candidate.last_modified and candidate.last_modified != previous.get("last_modified"):
            reasons.append("lastModified geändert")
        if reasons:
            changes.append(("AKTUALISIERT", candidate, "; ".join(reasons)))
    for repo_id, previous in known.items():
        if repo_id not in current and previous.get("present", True):
            changes.append((
                "ENTFERNT",
                candidate_from_state(repo_id, previous),
                "Repository ist nicht mehr unter den passenden API-Ergebnissen",
            ))
    return changes


def updated_state(
    candidates: Iterable[Candidate], old: dict[str, Any], checked_at: str,
    absent_retention_days: int = 0,
) -> dict[str, Any]:
    """Aktualisiert den Zustand und entfernt lange abwesende Einträge optional.

    Neu abwesende Repositories bleiben unabhängig von der Frist mindestens bis
    zum nächsten erfolgreichen Lauf erhalten. Der Wert 0 deaktiviert die
    Bereinigung vollständig.
    """
    state, _pruned = update_state_with_pruning(
        candidates, old, checked_at, absent_retention_days,
    )
    return state


def update_state_with_pruning(
    candidates: Iterable[Candidate], old: dict[str, Any], checked_at: str,
    absent_retention_days: int = 0,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Aktualisiert den Zustand und nennt entfernte Alt-Datensätze."""
    repositories = dict(old["repositories"])
    current = {candidate.repo_id: candidate for candidate in candidates}
    pruned: list[str] = []
    for repo_id, previous in list(repositories.items()):
        if repo_id not in current:
            if not previous.get("present", True) and absent_retention_days > 0:
                missing_since = parse_timestamp(previous.get("missing_since"))
                check_time = parse_timestamp(checked_at)
                if (
                    missing_since is not None
                    and check_time is not None
                    and check_time >= missing_since + timedelta(days=absent_retention_days)
                ):
                    del repositories[repo_id]
                    pruned.append(repo_id)
                    continue
            record = dict(previous)
            record["present"] = False
            record["missing_since"] = previous.get("missing_since") or checked_at
            repositories[repo_id] = record
    for candidate in current.values():
        previous = repositories.get(candidate.repo_id, {})
        repositories[candidate.repo_id] = {
            "sha": candidate.sha,
            "last_modified": candidate.last_modified,
            "first_seen": previous.get("first_seen", checked_at),
            "last_seen": checked_at,
            "present": True,
            "missing_since": None,
            "candidate": candidate_snapshot(candidate),
        }
    return (
        {
            "schema_version": STATE_SCHEMA_VERSION,
            "last_successful_check": checked_at,
            "repositories": repositories,
        },
        tuple(sorted(pruned, key=str.casefold)),
    )


def render_report(
    changes: list[Change], candidates: list[Candidate], rejected: list[str],
    checked_at: str, dry_run: bool, first_run: bool,
    pruned_repositories: Iterable[str] = (), absent_retention_days: int = 0,
) -> str:
    pruned = tuple(pruned_repositories)
    lines = [
        "LLM-Modellradar – Prüfbericht",
        "=" * 31,
        f"Prüfzeitpunkt (UTC): {checked_at}",
        f"Modus: {'Dry-Run (Zustand unverändert)' if dry_run else 'regulär'}",
        f"Passende Repositories: {len(candidates)}",
        f"Erkannte Lebenszyklusänderungen: {len(changes)}",
        f"{'Zur Bereinigung vorgemerkt' if dry_run else 'Bereinigte Zustandsdatensätze'}: {len(pruned)}",
    ]
    if first_run:
        lines.append("Hinweis: Es existierte noch kein lokaler Ausgangsbestand.")
    lines.extend(["", "Lebenszyklusereignisse", "-----------------------"])
    if not changes:
        lines.append("Keine Lebenszyklusänderungen bei passenden Repositories.")
    for status, item, change_reason in changes:
        lines.extend([
            "",
            f"[{status}] {item.repo_id}",
            f"  Herausgeber: {item.publisher}",
            f"  Familie: {item.family}",
            f"  Link: {item.url}",
            f"  Änderungsdatum: {item.last_modified or 'nicht von API geliefert'}",
            f"  Revision (SHA): {item.sha or 'nicht von API geliefert'}",
            "  Parametergröße: " + (
                f"{item.parameters_b:g}B ({item.parameter_source})"
                if item.parameters_b is not None else item.parameter_source
            ),
            f"  Typ: {item.repo_kind}",
            f"  Pipeline: {item.pipeline_tag or 'nicht angegeben'}",
            "  GGUF-Quantisierungen: " + (", ".join(item.quantizations) if item.quantizations else "nicht erkennbar"),
            f"  Erkennungsgrund: {change_reason}; {item.reason}",
            "  Bewertung: möglicher Nachfolger – keine automatische Eignungs- oder Qualitätsaussage",
        ])
    lines.extend(["", "Zustandsbereinigung", "-------------------"])
    if absent_retention_days == 0:
        lines.append("Deaktiviert: Abwesende Modelle werden unbegrenzt aufbewahrt.")
    elif not pruned:
        lines.append(
            f"Keine Datensätze haben die Aufbewahrungsfrist von {absent_retention_days} Tagen erreicht."
        )
    else:
        verb = "Würde entfernen" if dry_run else "Entfernt"
        lines.append(f"{verb} nach {absent_retention_days} Tagen Abwesenheit ({len(pruned)}):")
        lines.extend(f"  - {repo_id}" for repo_id in pruned)
        if not dry_run:
            lines.append(
                "Hinweis: Die Bereinigung wird erst nach erfolgreichem Schreiben "
                "der Zustandsdatei wirksam."
            )
    lines.extend(["", "Filterdiagnose", "---------------"])
    lines.append(f"Verworfene API-Ergebnisse: {len(rejected)}")
    lines.extend(f"  - {reason}" for reason in rejected[:100])
    if len(rejected) > 100:
        lines.append(f"  - … {len(rejected) - 100} weitere verworfene Ergebnisse")
    return "\n".join(lines) + "\n"


def notify(changes: Iterable[Change]) -> None:
    changes = list(changes)
    count = len(changes)
    names = ", ".join(item.repo_id for _, item, _ in changes[:3])
    if count > 3:
        names += f" und {count - 3} weitere"
    try:
        radar_platform.send_notification(
            "LLM-Modellradar: Änderungen", f"{count} Lebenszyklusänderungen: {names}",
        )
    except FileNotFoundError as exc:
        raise RadarError("notify-send wurde nicht gefunden.") from exc
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RadarError(f"Desktop-Benachrichtigung fehlgeschlagen: {exc}") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lokaler Radar für mögliche LLM-Nachfolger")
    parser.add_argument("--config", type=Path, default=default_config_path(), help="Pfad zur JSON-Konfiguration")
    parser.add_argument("--state", type=Path, help="Zustandspfad überschreiben")
    parser.add_argument("--report", type=Path, help="Berichtspfad überschreiben")
    parser.add_argument("--fixture", type=Path, help="lokale API-Testantwort statt Netzwerk verwenden")
    parser.add_argument("--dry-run", action="store_true", help="Zustand/Bericht nicht schreiben und nicht benachrichtigen")
    parser.add_argument("--no-notify", action="store_true", help="Benachrichtigung für diesen Lauf unterdrücken")
    parser.add_argument("--headless", action="store_true", help="Ohne Desktop, GTK oder Benachrichtigungen ausführen")
    parser.add_argument("--force", action="store_true", help="Prüfintervall einmalig ignorieren")
    parser.add_argument("--version", action="version", version=VERSION)
    return parser.parse_args(argv)


def run_check(
    config: dict[str, Any],
    state_path: Path,
    *,
    fixture_path: Path | None = None,
    dry_run: bool = False,
    force: bool = False,
    no_notify: bool = False,
    now: datetime | None = None,
    persist_migration: bool = False,
) -> CheckResult:
    """Führt eine Prüfung aus; nur explizite Schema-Migration darf schreiben."""
    state, first_run = load_state(state_path, persist_migration=persist_migration)
    interval_days = int(config.get("check_interval_days", 7))
    if not (dry_run or force or fixture_path) and not check_is_due(state, interval_days, now):
        return CheckResult(
            due=False,
            message=f"Noch nicht fällig: Das konfigurierte Intervall beträgt {interval_days} Tage.",
            first_run=first_run,
        )

    if fixture_path:
        models = load_json(fixture_path, "Testdaten")
        if not isinstance(models, list):
            raise RadarError("Testdaten müssen ein JSON-Array enthalten.")
    else:
        models = fetch_models(config)

    candidates, rejected = select_candidates(models, config)
    changes = compare(candidates, state)
    current = now or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    checked_at = current.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    retention_days = int(config.get("absent_retention_days", 365))
    new_state, pruned_repositories = update_state_with_pruning(
        candidates,
        state,
        checked_at,
        retention_days,
    )
    report = render_report(
        changes, candidates, rejected, checked_at, dry_run, first_run,
        pruned_repositories, retention_days,
    )
    notify_required = bool(
        changes
        and not dry_run
        and not no_notify
        and (not first_run or bool(config.get("notify_on_first_run", False)))
    )
    return CheckResult(
        due=True,
        message="Prüfung abgeschlossen.",
        report=report,
        new_state=new_state,
        changes=tuple(changes),
        candidates=tuple(candidates),
        rejected=tuple(rejected),
        pruned_repositories=pruned_repositories,
        checked_at=checked_at,
        first_run=first_run,
        notify_required=notify_required,
    )


def persist_check(result: CheckResult, state_path: Path, report_path: Path) -> None:
    """Veröffentlicht Bericht und Meldung, bevor der neue Zustand bestätigt wird.

    So bleibt ein Treffer bei jedem Fehler vor dem abschließenden Zustandswechsel
    für den nächsten Lauf offen. Ein Fehler beim Zustandsschreiben kann dadurch
    höchstens eine doppelte, aber keine verlorene Benachrichtigung verursachen.
    """
    if not result.due or result.new_state is None:
        raise RadarError("Ein nicht ausgeführter Prüflauf kann nicht gespeichert werden.")
    if state_path.expanduser().resolve(strict=False) == report_path.expanduser().resolve(strict=False):
        raise RadarError("Zustands- und Berichtsdatei müssen unterschiedlich sein.")
    atomic_write(report_path, result.report)
    if result.notify_required:
        notify(result.changes)
    atomic_write(
        state_path,
        json.dumps(result.new_state, indent=2, ensure_ascii=False) + "\n",
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_config(args.config.expanduser(), persist_migration=not args.dry_run)
        state_path = (args.state or Path(config["state_file"])).expanduser()
        report_path = (args.report or Path(config["report_file"])).expanduser()
        result = run_check(
            config,
            state_path,
            fixture_path=args.fixture,
            dry_run=args.dry_run,
            force=args.force,
            no_notify=(args.no_notify or args.headless or not radar_platform.desktop_notifications_available()),
            persist_migration=not args.dry_run,
        )
        if not result.due:
            print(result.message)
            return 0
        print(result.report, end="")
        if args.dry_run:
            return 0
        persist_check(result, state_path, report_path)
        return 0
    except RadarError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"FEHLER beim lokalen Dateizugriff: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
