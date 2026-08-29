"""Current SQLAlchemy metadata sizes the audit kind for any event type the contracts render."""

from __future__ import annotations

import re
import unittest

from aerial_rescue_contracts import namespace_prefix
from aerial_rescue_contracts.topics import MAX_KIND_LENGTH, Family
from aerial_rescue_store.database.schema import AUDIT_RECORD, EVENT_TYPE_LENGTH
from sqlalchemy import String


class AuditKindSchemaTests(unittest.TestCase):
    def test_the_kind_column_holds_the_longest_event_type_the_contracts_can_render(self) -> None:
        # Arrange
        prefix = namespace_prefix().replace("/", ".") + "."
        longest_type = max(
            len(prefix + re.sub(r"\{[^}]+\}", "k" * MAX_KIND_LENGTH, family.type_suffix))
            for family in Family
        )

        # Act
        kind = AUDIT_RECORD.c.kind
        kind_length = kind.type.length if isinstance(kind.type, String) else None

        # Assert
        self.assertEqual((96, True), (kind_length, longest_type <= EVENT_TYPE_LENGTH))
