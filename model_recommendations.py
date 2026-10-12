# SPDX-License-Identifier: GPL-3.0-or-later
"""Nachvollziehbare, rein lokale Regeln für Hardware-Empfehlungen.

Die Funktionen dieses Moduls laden keine Modelle und greifen nicht auf Hardware
zu. Sie bewerten ausschließlich übergebene Grafik- und Repository-Metadaten.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


GIB = 1024 ** 3
RUNTIME_FACTOR = 1.20
FIXED_RESERVE_BYTES = 512 * 1024 ** 2
QUANT_RE = re.compile(r"(?<![A-Z0-9])((?:IQ|Q|BF|F)\d(?:_[A-Z0-9]+)*(?:-[A-Z0-9]+)?)(?![A-Z0-9])", re.I)


@dataclass(frozen=True)
class Variant:
    filename: str
    quantization: str | None
    size_bytes: int | None


@dataclass(frozen=True)
class Recommendation:
    repo_id: str
    url: str
    rating: str
    variant: Variant | None
    required_bytes: int | None
    reason: str


def visible_dedicated_vram(graphics: dict[str, Any]) -> int | None:
    """Größter einzeln sichtbarer dedizierter VRAM; GPUs werden nie addiert."""
    values = [
        adapter.get("vram_bytes") for adapter in graphics.get("adapters", [])
        if isinstance(adapter, dict) and adapter.get("kind") == "dediziert"
        and isinstance(adapter.get("vram_bytes"), int) and adapter["vram_bytes"] > 0
    ]
    return max(values, default=None)


def gguf_variants(model: dict[str, Any]) -> tuple[Variant, ...]:
    """Liest GGUF-Dateinamen und optionale Größen aus Hub-Metadaten."""
    variants: list[Variant] = []
    for sibling in model.get("siblings", []):
        if not isinstance(sibling, dict):
            continue
        filename = sibling.get("rfilename")
        if not isinstance(filename, str) or not filename.lower().endswith(".gguf"):
            continue
        raw_size = sibling.get("size")
        if raw_size is None and isinstance(sibling.get("lfs"), dict):
            raw_size = sibling["lfs"].get("size")
        size = raw_size if isinstance(raw_size, int) and raw_size > 0 else None
        match = QUANT_RE.search(Path(filename).name)
        variants.append(Variant(filename, match.group(1).upper() if match else None, size))
    return tuple(sorted(variants, key=lambda item: (item.size_bytes is None, item.size_bytes or 0, item.filename)))


def recommend(models: Iterable[dict[str, Any]], graphics: dict[str, Any]) -> tuple[list[Recommendation], list[str]]:
    """Bewertet konkrete GGUF-Dateien gegen sichtbaren dedizierten VRAM."""
    vram = visible_dedicated_vram(graphics)
    notes = [
        "Es werden ausschließlich Metadaten bewertet; Modelle werden weder heruntergeladen noch ausgeführt.",
        "Richtwert: Dateigröße × 1,20 + 512 MiB Reserve. Kontextlänge, Backend und weitere Programme können mehr Speicher benötigen.",
        "Mehrere GPUs werden nicht zusammengerechnet; bewertet wird die größte einzeln sichtbare dedizierte GPU.",
    ]
    if vram is None:
        notes.append("Kein verlässlicher dedizierter VRAM sichtbar; daher keine automatische Eignungszusage.")
    recommendations: list[Recommendation] = []
    for model in models:
        repo_id = str(model.get("id") or model.get("modelId") or "")
        if "/" not in repo_id:
            continue
        variants = gguf_variants(model)
        if not variants:
            recommendations.append(Recommendation(repo_id, f"https://huggingface.co/{repo_id}", "UNBEKANNT", None, None, "Keine GGUF-Datei in den verfügbaren Metadaten; eine VRAM-Bewertung ist nicht möglich."))
            continue
        for variant in variants:
            if variant.size_bytes is None:
                recommendations.append(Recommendation(repo_id, f"https://huggingface.co/{repo_id}", "UNBEKANNT", variant, None, "Die Hub-Metadaten nennen keine Dateigröße; keine Schätzung nur aus der Parameterzahl."))
                continue
            required = int(variant.size_bytes * RUNTIME_FACTOR) + FIXED_RESERVE_BYTES
            if vram is None:
                rating, reason = "UNBEKANNT", "Dateigröße bekannt, aber kein verlässlicher dedizierter VRAM sichtbar."
            else:
                ratio = required / vram
                if ratio <= 0.70:
                    rating, reason = "GUT PASSEND", "Konservative Reserve für Kontext und Laufzeitpuffer vorhanden."
                elif ratio <= 0.85:
                    rating, reason = "EINGESCHRÄNKT", "Wenig Reserve; kürzerer Kontext oder CPU/GPU-Hybridbetrieb kann nötig sein."
                else:
                    rating, reason = "NICHT EMPFOHLEN", "Der konservative Speicherbedarf überschreitet die verfügbare Reserve."
            recommendations.append(Recommendation(repo_id, f"https://huggingface.co/{repo_id}", rating, variant, required, reason))
    order = {"GUT PASSEND": 0, "EINGESCHRÄNKT": 1, "UNBEKANNT": 2, "NICHT EMPFOHLEN": 3}
    recommendations.sort(key=lambda item: (order[item.rating], item.repo_id.lower(), item.variant.filename if item.variant else ""))
    return recommendations, notes


def render_report(models: Iterable[dict[str, Any]], graphics: dict[str, Any]) -> str:
    recommendations, notes = recommend(models, graphics)
    vram = visible_dedicated_vram(graphics)
    lines = ["LLM-Hardware-Eignung (konservative Metadatenbewertung)", "=" * 55,
             f"Sichtbarer dedizierter VRAM: {vram / GIB:.1f} GiB" if vram else "Sichtbarer dedizierter VRAM: unbekannt", ""]
    lines.extend(f"Hinweis: {note}" for note in notes)
    lines.append("")
    if not recommendations:
        lines.append("Keine freigegebenen Modellkandidaten mit bewertbaren Metadaten gefunden.")
    for item in recommendations:
        variant = item.variant
        file_text = variant.filename if variant else "keine GGUF-Variante"
        quant = f", {variant.quantization}" if variant and variant.quantization else ""
        size = f", Datei: {variant.size_bytes / GIB:.2f} GiB" if variant and variant.size_bytes else ""
        required = f", Richtwert: {item.required_bytes / GIB:.2f} GiB" if item.required_bytes else ""
        lines.extend((f"[{item.rating}] {item.repo_id}", f"  Variante: {file_text}{quant}{size}{required}", f"  Begründung: {item.reason}", f"  Modellkarte: {item.url}"))
    return "\n".join(lines) + "\n"
