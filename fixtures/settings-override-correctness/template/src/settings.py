"""Settings merge behavior."""


def merge_settings(defaults, overrides):
    """Merge known settings, treating missing and None as use-default."""
    return {
        key: overrides.get(key) or default
        for key, default in defaults.items()
    }
