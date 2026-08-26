"""Actor context shared by the service layer."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from taxstamp.audit import AuditActor
from taxstamp.enums import Role
from taxstamp.errors import Forbidden


@dataclass(frozen=True, slots=True)
class Actor:
    principal_id: uuid.UUID
    subject: str
    role: Role
    company_id: uuid.UUID | None
    request_id: str | None = None

    def audit_actor(self) -> AuditActor:
        return AuditActor(
            principal_id=self.principal_id,
            subject=self.subject,
            role=self.role.value,
            company_id=self.company_id,
        )

    def require_role(self, *roles: Role) -> None:
        if self.role not in roles:
            raise Forbidden(
                f"role {self.role.value} may not perform this action",
                detail={"required": ",".join(role.value for role in roles)},
            )

    def require_company(self, company_id: uuid.UUID) -> None:
        """Deny cross-tenant access. Staff roles are not implicitly cross-tenant."""
        if self.role is Role.ADMIN:
            return
        if self.company_id is None or self.company_id != company_id:
            raise Forbidden("resource belongs to another company")

    def require_subject(self, subject: str, *, resource: str) -> None:
        """Bind a caller-provided identity attribute to the authenticated principal."""
        if self.subject != subject:
            raise Forbidden(f"{resource} must match the authenticated principal")
