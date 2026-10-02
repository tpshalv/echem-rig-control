"""Numeric state channels for histories, with readable display/export labels."""

REGULATION_MODE_UNIT = "0=OFF,1=CC,2=CV"
OUTPUT_STATE_UNIT = "0=OFF,1=ON"


def state_label(value: float, unit: str) -> str | float:
    labels = {
        REGULATION_MODE_UNIT: {0: "OFF", 1: "CC", 2: "CV"},
        OUTPUT_STATE_UNIT: {0: "OFF", 1: "ON"},
    }.get(unit)
    return value if labels is None else labels.get(value, "UNKNOWN")
