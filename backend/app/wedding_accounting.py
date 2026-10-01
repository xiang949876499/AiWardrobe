"""Durable account balances and local authoritative bill reconciliation.

All callers lock project before account; deleted projects require only account.
No provider request, portrait or source text belongs in these durable receipts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select, update, func

from app.config import get_settings
from app.models import WeddingAccountUsage, WeddingBillingReceipt, WeddingProject, User


def lock_account(db, owner_id):
    db.execute(select(User.id).where(User.id == owner_id).with_for_update()).scalar_one()
    if db.get_bind().dialect.name == 'sqlite':
        db.execute(update(User).where(User.id == owner_id).values(updated_at=User.updated_at))
    account = db.get(WeddingAccountUsage, owner_id)
    if account is None:
        spent, reserved = db.execute(select(func.coalesce(func.sum(WeddingProject.budget_spent_fen), 0),
            func.coalesce(func.sum(WeddingProject.budget_reserved_fen), 0)).where(WeddingProject.owner_id == owner_id)).one()
        account = WeddingAccountUsage(owner_id=owner_id, spent_fen=spent, reserved_fen=reserved)
        db.add(account)
        db.flush()
    else:
        db.refresh(account)
    return account


def reserve(db, project, amount):
    account = lock_account(db, project.owner_id)
    if account.spent_fen + account.reserved_fen + amount > get_settings().wedding_account_budget_limit_fen:
        raise HTTPException(409, 'ACCOUNT_BUDGET_LIMIT_EXCEEDED')
    if project.budget_spent_fen + project.budget_reserved_fen + project.budget_delivery_reserve_fen + amount > project.budget_limit_fen:
        raise HTTPException(409, 'BUDGET_LIMIT_EXCEEDED')
    account.reserved_fen += amount
    project.budget_reserved_fen += amount
    db.flush()


def settle(db, project, reserved, actual):
    account = lock_account(db, project.owner_id)
    if reserved < 0 or actual < 0 or account.reserved_fen < reserved or project.budget_reserved_fen < reserved:
        raise ValueError('Accounting reservation inconsistent')
    account.reserved_fen -= reserved
    account.spent_fen += actual
    project.budget_reserved_fen -= reserved
    project.budget_spent_fen += actual
    db.flush()


def settle_deleted_video(db, receipt):
    """Close deleted-job envelope only after every dispatched operation is known."""
    rows = list(db.scalars(select(WeddingBillingReceipt).where(WeddingBillingReceipt.job_id == receipt.job_id)))
    envelope = next((r for r in rows if r.provider in ('budget_envelope','legacy_unknown')), None)
    operations = [r for r in rows if r is not envelope]
    if not envelope or envelope.settled or any(r.state in ('dispatching','unknown','running') for r in operations):
        return
    account = lock_account(db, receipt.owner_id)
    # Re-read after owner serialization, so concurrent late callbacks settle once.
    db.refresh(envelope)
    if envelope.settled:
        return
    amount = sum(r.billed_fen if r.billed_fen is not None else r.estimated_fen for r in operations)
    account.reserved_fen -= envelope.reserved_fen
    account.spent_fen += amount
    envelope.estimated_fen, envelope.settled, envelope.state = amount, True, 'succeeded'
    for row in operations:
        row.settled = True
    db.flush()


def reconcile_rows(factory, rows):
    """Import exact provider task IDs; repeated equal bills are no-ops, conflicts reported."""
    results = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('task_id'), str) or type(row.get('cost_fen')) is not int or row['cost_fen'] < 0:
            results.append({'status': 'invalid'})
            continue
        with factory() as db:
            matches = list(db.scalars(select(WeddingBillingReceipt).where(WeddingBillingReceipt.task_id == row['task_id'])))
            if len(matches) != 1:
                results.append({'task_id': row['task_id'], 'status': 'unmatched' if not matches else 'ambiguous'})
                continue
            receipt = matches[0]
            project = db.scalar(select(WeddingProject).where(WeddingProject.id == receipt.project_id).with_for_update())
            account = lock_account(db, receipt.owner_id)
            db.refresh(receipt, with_for_update=True)
            if receipt.billed_fen is not None:
                results.append({'task_id': row['task_id'], 'status': 'duplicate' if receipt.billed_fen == row['cost_fen'] else 'conflict'})
                continue
            if not receipt.settled:
                if project is not None:
                    results.append({'task_id': row['task_id'], 'status': 'unsettled_requires_review'})
                    continue
                # An authoritative bill can resolve a deleted operation without
                # reviving private project state or querying a provider.
                receipt.billed_fen, receipt.state = row['cost_fen'], 'billed'
                envelope = db.scalar(select(WeddingBillingReceipt).where(WeddingBillingReceipt.job_id == receipt.job_id,
                    WeddingBillingReceipt.provider.in_(['budget_envelope','legacy_unknown'])))
                if envelope:
                    settle_deleted_video(db, receipt)
                else:
                    account.reserved_fen -= receipt.reserved_fen
                    account.spent_fen += receipt.billed_fen
                    receipt.settled = True
                db.commit()
                results.append({'task_id': row['task_id'], 'status': 'applied',
                    'difference_fen': row['cost_fen'] - receipt.estimated_fen,
                    'mismatch': row['cost_fen'] != receipt.estimated_fen,
                    'over_cap': account.spent_fen + account.reserved_fen > get_settings().wedding_account_budget_limit_fen})
                continue
            delta = row['cost_fen'] - receipt.estimated_fen
            account.spent_fen += delta
            if project:
                project.budget_spent_fen += delta
            receipt.billed_fen = row['cost_fen']
            over_cap = account.spent_fen + account.reserved_fen > get_settings().wedding_account_budget_limit_fen or bool(project and project.budget_spent_fen + project.budget_reserved_fen + project.budget_delivery_reserve_fen > project.budget_limit_fen)
            db.commit()
            results.append({'task_id': row['task_id'], 'status': 'applied', 'difference_fen': delta, 'mismatch': delta != 0, 'over_cap': over_cap})
    return results


def main():
    from app.database import init_db, get_session_factory
    parser = argparse.ArgumentParser(description='Import authoritative provider task costs from local JSON, without provider calls')
    parser.add_argument('file', type=Path)
    args = parser.parse_args()
    rows = json.loads(args.file.read_text(encoding='utf-8'))
    if not isinstance(rows, list) or len(rows) > 10000:
        parser.error('Expected at most 10000 task-id/cost rows')
    init_db()
    print(json.dumps(reconcile_rows(get_session_factory(), rows)))


if __name__ == '__main__':
    main()
