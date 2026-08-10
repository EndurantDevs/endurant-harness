"""Select requested records while preserving first-match semantics."""


def select_records(records, requested_ids):
    """Return the first matching record for every requested identifier."""
    selected = []
    for requested_id in requested_ids:
        for record in records:
            if record["id"] == requested_id:
                selected.append(record)
                break
    return selected
