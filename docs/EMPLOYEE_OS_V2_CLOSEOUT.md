# Employee OS V2 Management Closeout

Status: FROZEN FOR ROUTINE CHANGES

Employee OS management is complete for owner-operated employee identity and
access administration. Future changes to the closed surface require either a
confirmed defect or an approved new Employee OS scope.

## Closed surface

- List, search and filter every canonical Employee V2 record.
- Create and edit employee identity and active/inactive status.
- Link, unlink, create and password-reset non-owner team login accounts.
- Revoke an inactive or unlinked employee's authenticated access immediately.
- Assign canonical operational roles, including assigned-work-only preparation.
- Display migrated salary, advance and custody facts without writing them.
- Preserve actor, time, changed fields and before/after state in the audit trail.
- Support responsive employee cards and owner-only management actions.

## Frozen invariants

- Employee identity is tenant-scoped and separate from login and salary.
- The owner account cannot be selected or managed as an employee login.
- Employee management never writes `operating_salaries`, liabilities, advances,
  custody or `general_ledger`.
- Password plaintext never enters audit events, API responses or logs.
- Preparation access remains exactly `preparation.assigned.read` and
  `preparation.assigned.work` unless a later approved role decision replaces it.

## Deferred scope

- Salary contract editing and payroll execution.
- Legacy payroll cutover and legacy employee-page retirement.
- Final production login validation for a named employee requires that person's
  confirmed account and a privately supplied or owner-reset credential.
