"""JSON schema for the structured extraction. Written for OpenAI structured
outputs in strict mode: every property is required and additionalProperties is
false, so nullability is expressed via ["type", "null"] unions rather than by
omitting fields."""
from __future__ import annotations

SCHEMA_NAME = "receipt_extraction"


def _nullable(*types: str) -> dict:
    return {"type": [*types, "null"]}


RECEIPT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "vendor", "date", "currency", "line_items", "subtotal", "tax",
        "service_charge", "discount", "rounding_adjustment", "total",
        "tax_mode", "legibility", "notes",
    ],
    "properties": {
        "vendor": {**_nullable("string"), "description": "Merchant / business name as printed."},
        "date": {**_nullable("string"), "description": "Transaction date in ISO 8601 YYYY-MM-DD."},
        "currency": {**_nullable("string"), "description": "ISO 4217 code, e.g. MYR. RM -> MYR."},
        "line_items": {
            "type": "array",
            "description": "One entry per printed line item. Empty array if none are legible.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["description", "qty", "unit_price", "amount"],
                "properties": {
                    "description": _nullable("string"),
                    "qty": _nullable("number"),
                    "unit_price": _nullable("number"),
                    "amount": {**_nullable("number"), "description": "Printed line total."},
                },
            },
        },
        "subtotal": _nullable("number"),
        "tax": {**_nullable("number"), "description": "Total tax/GST amount as printed."},
        "service_charge": _nullable("number"),
        "discount": _nullable("number"),
        "rounding_adjustment": {**_nullable("number"), "description": "May be negative."},
        "total": {**_nullable("number"), "description": "Final amount payable as printed."},
        "tax_mode": {
            "type": "string",
            "enum": ["inclusive", "exclusive", "unknown"],
            "description": "Whether tax is included in or added to the total.",
        },
        "legibility": {
            "type": "string",
            "enum": ["high", "medium", "low"],
            "description": "Model's honest read-quality self-report for this image.",
        },
        "notes": _nullable("string"),
    },
}
