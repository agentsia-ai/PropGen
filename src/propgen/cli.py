"""PropGen CLI — `propgen ...` (persona names live in agentsia-core only)."""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from propgen import __version__
from propgen._time import format_local
from propgen.config.loader import db_sqlite_path, load_api_keys, load_config
from propgen.crm.database import ProposalDatabase
from propgen.models import ProposalStatus
from propgen.pricing.catalog import load_catalog_entries
from propgen.service import (
    approve_for_send,
    create_proposal_explicit,
    create_proposal_from_appointment,
    create_proposal_from_lead,
    draft_next_followup,
    mark_declined,
    record_signed_manual,
    render_current_pdf,
    resolve_proposal_id,
    revise_proposal,
    send_proposal,
    sweep_expired,
)
from propgen.sources import docusign as docusign_api

console = Console()


def _log(debug: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def _boot():
    cfg = load_config()
    keys = load_api_keys()
    database = ProposalDatabase(db_sqlite_path(cfg))
    await database.init()
    return cfg, keys, database


@click.group()
@click.version_option(__version__, prog_name="propgen")
@click.option("--debug", is_flag=True)
def main(debug: bool) -> None:
    """PropGen — proposals, quotes, PDFs, DocuSign (human approval by default)."""
    _log(debug)


@main.command()
def pipeline() -> None:
    """Summary counts by proposal status."""

    async def _run() -> None:
        _, _, database = await _boot()
        counts = await database.pipeline_counts()
        table = Table(title="Proposal pipeline")
        table.add_column("Status", style="cyan")
        table.add_column("Count", justify="right", style="yellow")
        # Empty DB: GROUP BY returns no rows — still show every lifecycle bucket at 0.
        for st in ProposalStatus:
            table.add_row(st.value, str(int(counts.get(st.value, 0))))
        total = sum(counts.values())
        table.add_section()
        table.add_row("Total", str(int(total)), style="bold")
        console.print(table)

    asyncio.run(_run())


@main.command(name="list")
@click.option("--status", type=str, default=None)
@click.option("--limit", default=50, type=int)
def list_(status: Optional[str], limit: int) -> None:
    async def _run() -> None:
        cfg, _, database = await _boot()
        st = ProposalStatus(status) if status else None
        rows = await database.list_proposals(status=st, limit=limit)
        if not rows:
            console.print("[yellow]No proposals.[/yellow]")
            return
        table = Table(title="Proposals")
        table.add_column("ID", style="cyan")
        table.add_column("Status")
        table.add_column("Client")
        table.add_column("Subject")
        for p in rows:
            table.add_row(
                p.id[:8],
                p.status.value,
                p.client.email or p.client.name,
                (p.subject or "")[:50],
            )
        console.print(table)

    asyncio.run(_run())


@main.command()
@click.argument("proposal_id")
def show(proposal_id: str) -> None:
    async def _run() -> None:
        cfg, _, database = await _boot()
        pid = await resolve_proposal_id(database, proposal_id)
        if not pid:
            console.print("[red]Not found.[/red]")
            return
        p = await database.get_proposal(pid)
        if not p:
            console.print("[red]Not found.[/red]")
            return
        ver = await database.get_current_version(pid)
        console.print(f"[bold]{p.subject}[/bold]  [dim]{p.id}[/dim]")
        console.print(f"Status: {p.status.value}  Total: {p.total} {p.currency}")
        if p.expires_at:
            console.print(
                "Valid until: " + format_local(p.expires_at, cfg.business.timezone)
            )
        if ver:
            console.print(f"[dim]Version {ver.version_number} — {ver.pdf_path}[/dim]")

    asyncio.run(_run())


@main.command(name="from-lead")
@click.argument("lead_id")
def from_lead(lead_id: str) -> None:
    async def _run() -> None:
        cfg, keys, database = await _boot()
        from propgen.ai.drafter import ProposalDrafter

        r = await create_proposal_from_lead(cfg, keys, database, lead_id, ProposalDrafter)
        if not r:
            console.print("[red]Could not load lead (cross_engine.leadgen_db?)[/red]")
            return
        p, v = r
        console.print(f"[green]Created[/green] proposal {p.id} version {v.id}")

    asyncio.run(_run())


@main.command(name="from-appointment")
@click.argument("appointment_id")
def from_appointment(appointment_id: str) -> None:
    async def _run() -> None:
        cfg, keys, database = await _boot()
        from propgen.ai.drafter import ProposalDrafter

        r = await create_proposal_from_appointment(
            cfg, keys, database, appointment_id, ProposalDrafter
        )
        if not r:
            console.print("[red]Could not load appointment.[/red]")
            return
        p, v = r
        console.print(f"[green]Created[/green] proposal {p.id} version {v.id}")

    asyncio.run(_run())


@main.command()
@click.argument("proposal_id")
@click.option("--guidance", default="")
def revise(proposal_id: str, guidance: str) -> None:
    async def _run() -> None:
        cfg, keys, database = await _boot()
        from propgen.ai.drafter import ProposalDrafter

        pid = await resolve_proposal_id(database, proposal_id)
        if not pid:
            console.print("[red]Not found.[/red]")
            return
        ver = await revise_proposal(cfg, keys, database, pid, guidance, ProposalDrafter)
        if ver:
            console.print(f"[green]New version[/green] {ver.id}")
        else:
            console.print("[red]Revise failed.[/red]")

    asyncio.run(_run())


@main.command()
@click.argument("proposal_id")
def render(proposal_id: str) -> None:
    async def _run() -> None:
        cfg, _, database = await _boot()
        pid = await resolve_proposal_id(database, proposal_id)
        if not pid:
            console.print("[red]Not found.[/red]")
            return
        path = await render_current_pdf(cfg, database, pid)
        console.print(f"[green]Rendered[/green] {path}")

    asyncio.run(_run())


@main.command()
@click.argument("proposal_id")
def approve(proposal_id: str) -> None:
    async def _run() -> None:
        _, _, database = await _boot()
        pid = await resolve_proposal_id(database, proposal_id)
        if not pid:
            console.print("[red]Not found.[/red]")
            return
        out = await approve_for_send(database, pid)
        if not out:
            console.print("[red]Approve failed.[/red]")
            return
        _, tok = out
        console.print("[green]Approved.[/green] Use this token with send:")
        console.print(tok)

    asyncio.run(_run())


@main.command()
@click.argument("proposal_id")
@click.argument("approval_token")
def send(proposal_id: str, approval_token: str) -> None:
    async def _run() -> None:
        cfg, keys, database = await _boot()
        pid = await resolve_proposal_id(database, proposal_id)
        if not pid:
            console.print("[red]Not found.[/red]")
            return
        result = await send_proposal(cfg, keys, database, pid, approval_token)
        if result.get("ok"):
            console.print(f"[green]Sent[/green] envelope {result.get('envelope_id')}")
        else:
            console.print(f"[red]Send failed:[/red] {result}")

    asyncio.run(_run())


@main.command(name="record-signed")
@click.argument("proposal_id")
def record_signed(proposal_id: str) -> None:
    async def _run() -> None:
        _, _, database = await _boot()
        pid = await resolve_proposal_id(database, proposal_id)
        if not pid:
            console.print("[red]Not found.[/red]")
            return
        ok = await record_signed_manual(database, pid)
        console.print("[green]Recorded.[/green]" if ok else "[red]Failed.[/red]")

    asyncio.run(_run())


@main.command(name="mark-declined")
@click.argument("proposal_id")
def mark_declined_cmd(proposal_id: str) -> None:
    async def _run() -> None:
        _, _, database = await _boot()
        pid = await resolve_proposal_id(database, proposal_id)
        if not pid:
            console.print("[red]Not found.[/red]")
            return
        ok = await mark_declined(database, pid)
        console.print("[green]Marked declined.[/green]" if ok else "[red]Failed.[/red]")

    asyncio.run(_run())


@main.command(name="mark-expired")
def mark_expired_cmd() -> None:
    async def _run() -> None:
        cfg, _, database = await _boot()
        n = await sweep_expired(cfg, database)
        console.print(f"[green]Marked {n} expired.[/green]")

    asyncio.run(_run())


@main.command(name="draft-followups")
@click.argument("proposal_id")
def draft_followups_cmd(proposal_id: str) -> None:
    async def _run() -> None:
        cfg, keys, database = await _boot()
        from propgen.ai.drafter import ProposalDrafter

        pid = await resolve_proposal_id(database, proposal_id)
        if not pid:
            console.print("[red]Not found.[/red]")
            return
        rec = await draft_next_followup(cfg, keys, database, pid, ProposalDrafter)
        if rec:
            console.print(f"[green]Drafted follow-up[/green] {rec.id}")
            console.print(rec.draft_subject)
            console.print(rec.draft_body)
        else:
            console.print("[yellow]No pending follow-up row.[/yellow]")

    asyncio.run(_run())


@main.command()
def catalog() -> None:
    async def _run() -> None:
        cfg, _, _ = await _boot()
        for e in load_catalog_entries(cfg):
            console.print(f"{e.slug}: {e.name} — {e.unit_price} ({e.billing_kind.value})")

    asyncio.run(_run())


@main.group()
def docusign() -> None:
    """DocuSign connectivity checks."""


@docusign.command()
def ping() -> None:
    """Verify JWT configuration (obtains an access token)."""

    async def _run() -> None:
        cfg, keys, _ = await _boot()
        try:
            result = await docusign_api.docusign_ping(cfg, keys)
            console.print(result)
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]{e}[/red]")
            sys.exit(1)

    asyncio.run(_run())


@main.command()
def mcp() -> None:
    """Start MCP stdio server (see docs/MCP_SETUP.md)."""
    from propgen.mcp_server.server import main as mcp_main

    asyncio.run(mcp_main())


if __name__ == "__main__":
    main()
