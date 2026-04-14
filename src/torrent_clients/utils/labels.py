"""Label mutation helpers shared by downstream projects."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Protocol, runtime_checkable


@runtime_checkable
class SupportsSetLabels(Protocol):
    """Capability protocol for writing labels to torrent clients."""

    def set_labels(self, torrent: Any, labels: list[str]) -> None:
        """Set labels for a torrent."""


def _normalize_unique(labels: Iterable[str] | None) -> list[str]:
    unique_labels: list[str] = []
    seen: set[str] = set()
    if labels is None:
        return unique_labels

    for label in labels:
        text = str(label).strip()
        if not text or text in seen:
            continue
        unique_labels.append(text)
        seen.add(text)
    return unique_labels


def compute_labels(
    current: Iterable[str] | None,
    *,
    replace_prefix: Mapping[str, str] | None = None,
    add: Iterable[str] | None = None,
    remove: Iterable[str] | None = None,
) -> list[str]:
    """
    Compute next labels by applying remove, prefix replacement and append rules.

    The output keeps insertion order and removes duplicates.
    """
    labels = _normalize_unique(current)
    remove_set = set(_normalize_unique(remove))

    if remove_set:
        labels = [label for label in labels if label not in remove_set]

    if replace_prefix:
        for prefix, target in replace_prefix.items():
            target_label = str(target).strip()
            labels = [
                label
                for label in labels
                if not (label.startswith(prefix) and label != target_label)
            ]
            if target_label and target_label not in labels:
                labels.append(target_label)

    for label in _normalize_unique(add):
        if label not in labels:
            labels.append(label)

    return labels


def apply_labels_by_rule(
    *,
    client: SupportsSetLabels,
    torrent: Any,
    replace_prefix: Mapping[str, str] | None = None,
    add: Iterable[str] | None = None,
    remove: Iterable[str] | None = None,
) -> bool:
    """
    Apply computed labels to torrent and client only when labels changed.

    Returns:
        True when labels were updated, otherwise False.
    """
    current_labels = _normalize_unique(getattr(torrent, "labels", None))
    next_labels = compute_labels(
        current=current_labels,
        replace_prefix=replace_prefix,
        add=add,
        remove=remove,
    )

    if set(next_labels) == set(current_labels):
        return False

    client.set_labels(torrent, next_labels)
    torrent.labels = next_labels
    return True
